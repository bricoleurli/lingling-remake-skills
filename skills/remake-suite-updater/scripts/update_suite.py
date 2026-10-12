"""Update only the named suite skills. Python 3.10+, standard library only."""
import argparse, contextlib, hashlib, io, json, os, shutil, stat, tempfile, urllib.request, zipfile
from pathlib import Path

REPO = 'bricoleurli/lingling-remake-skills'
NAMES = ('remake-studio', 'flow-spec-builder', 'remake-workbench-builder', 'remake-suite-updater')
IGNORED = {'__pycache__', '.DS_Store'}

def read(p):
    return json.loads(p.read_text(encoding='utf-8'))

def write(p, data):
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, p)

def hashes(root):
    if root.is_symlink():
        raise ValueError('拒绝符号链接目录：' + str(root))
    result = {}
    if not root.exists():
        return result
    for p in sorted(root.rglob('*')):
        if p.is_symlink():
            raise ValueError('拒绝符号链接：' + str(p))
        if any(x in IGNORED for x in p.relative_to(root).parts) or p.suffix == '.pyc':
            continue
        if p.is_file():
            result[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return result

def validate(source):
    manifest = read(source / 'suite-release.json')
    if manifest.get('format') != 1 or set(manifest.get('skills', {})) != set(NAMES):
        raise ValueError('发布清单格式或技能列表不正确')
    for name in NAMES:
        info = manifest['skills'][name]
        if not info.get('version') or 'SKILL.md' not in info['files']:
            raise ValueError('发布清单缺少版本或 SKILL.md')
        if hashes(source / 'skills' / name) != info['files']:
            raise ValueError('发布文件校验失败：' + name)
    return manifest

def download(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'lingling-remake-updater'})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read(32 * 1024 * 1024 + 1)
    if len(data) > 32 * 1024 * 1024:
        raise ValueError('下载超过大小限制')
    return data

def fetch(dest):
    # Resolve stable once, then download immutable commit to avoid mixed releases.
    sha = json.loads(download(f'https://api.github.com/repos/{REPO}/commits/stable'))['sha']
    if len(sha) != 40 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('仓库返回无效版本')
    data = download(f'https://codeload.github.com/{REPO}/zip/{sha}')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(x.file_size for x in archive.infolist()) > 100 * 1024 * 1024:
            raise ValueError('解压大小超过限制')
        for info in archive.infolist():
            p = Path(info.filename)
            if p.is_absolute() or '..' in p.parts or '\\' in info.filename or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('压缩包含不安全路径')
        archive.extractall(dest)
    roots = list(dest.iterdir())
    if len(roots) != 1:
        raise ValueError('压缩包目录结构异常')
    return roots[0], sha

def locations(target):
    identity = hashlib.sha256(str(target).encode()).hexdigest()[:12]
    home = target.parent / ('.remake-suite-' + identity)
    if home.is_symlink():
        raise ValueError('管理目录不可为符号链接')
    home.mkdir(parents=True, exist_ok=True)
    return home

@contextlib.contextmanager
def locked(home):
    lock = home / 'lock'
    try:
        lock.mkdir()
    except FileExistsError:
        raise ValueError('另一更新可能正在运行；若进程已退出，请由 Agent 检查锁目录：' + str(lock))
    try:
        yield
    finally:
        lock.rmdir()

def replace_all(target, desired, state, state_path, home, record_backup=False):
    backup = Path(tempfile.mkdtemp(prefix='backup-', dir=home))
    before = {name: hashes(target / name) if (target / name).exists() else None for name in NAMES}
    oldstate = read(state_path) if state_path.exists() else None
    for name in NAMES:
        if before[name] is not None:
            shutil.copytree(target / name, backup / name)
    write(backup / 'snapshot.json', {'before': before, 'state': oldstate})
    if record_backup:
        state['last_backup'] = str(backup)
    # Stage every new directory before mutating the installation.
    with tempfile.TemporaryDirectory(prefix='stage-', dir=home) as tmp:
        stage = Path(tmp)
        for name, source in desired.items():
            if source is not None:
                shutil.copytree(source, stage / name)
        changed = []
        try:
            for name in NAMES:
                changed.append(name)
                current = target / name
                if current.exists():
                    shutil.rmtree(current)
                if desired[name] is not None:
                    shutil.move(str(stage / name), current)
            if state is None:
                state_path.unlink(missing_ok=True)
            else:
                write(state_path, state)
        except Exception:
            for name in changed:
                current = target / name
                if current.exists():
                    shutil.rmtree(current)
                if before[name] is not None:
                    shutil.copytree(backup / name, current)
            if oldstate is None:
                state_path.unlink(missing_ok=True)
            else:
                write(state_path, oldstate)
            raise
    return backup

def install(source, target, home, apply=False, accept=False, commit='local'):
    manifest = validate(source)
    state_path = home / 'state.json'
    state = read(state_path) if state_path.exists() else {'skills': {}}
    conflicts, changes = [], []
    for name in NAMES:
        dest = target / name
        current = hashes(dest)
        expected = manifest['skills'][name]['files']
        baseline = state.get('skills', {}).get(name, {}).get('files')
        if dest.exists() and current != expected and current != baseline:
            conflicts.append(name)
        if not dest.exists() or current != expected:
            changes.append(name)
    result = {'release': manifest['version'], 'commit': commit, 'changes': changes,
              'local_or_unmanaged_changes': conflicts, 'applied': False}
    if not apply:
        return result
    if not changes:
        adopted = {'version': manifest['version'], 'commit': commit, 'skills': manifest['skills']}
        if state.get('last_backup'):
            adopted['last_backup'] = state['last_backup']
        write(state_path, adopted)
        return result
    if conflicts and not accept:
        raise ValueError('存在本地改动或未接管旧安装：' + ', '.join(conflicts) + '。先检查差异；用户同意替换后才可使用 --accept-existing。')
    newstate = {'version': manifest['version'], 'commit': commit, 'skills': manifest['skills']}
    backup = replace_all(target, {n: source / 'skills' / n for n in NAMES}, newstate, state_path, home, record_backup=True)
    result.update(applied=True, backup=str(backup))
    return result

def rollback(target, home):
    state_path = home / 'state.json'
    state = read(state_path)
    backup = Path(state['last_backup'])
    if backup.parent != home or backup.is_symlink():
        raise ValueError('无效备份位置')
    snapshot = read(backup / 'snapshot.json')
    for name in NAMES:
        if hashes(target / name) != state['skills'][name]['files']:
            raise ValueError('安装后存在本地改动，停止回滚：' + name)
        if snapshot['before'][name] is not None and hashes(backup / name) != snapshot['before'][name]:
            raise ValueError('备份校验失败：' + name)
    safety = replace_all(target, {n: backup / n if snapshot['before'][n] is not None else None for n in NAMES}, snapshot['state'], state_path, home)
    return {'rolled_back': True, 'backup_of_replaced_version': str(safety)}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skills-dir', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--accept-existing', action='store_true')
    parser.add_argument('--rollback', action='store_true')
    parser.add_argument('--source', type=Path, help='已下载且可信的本地仓库；默认从官方 stable 获取')
    args = parser.parse_args()
    target = args.skills_dir.expanduser().absolute()
    if any(p.is_symlink() for p in [target, *target.parents]):
        raise ValueError('目标或父目录是符号链接，请先确认实际安装位置')
    target.mkdir(parents=True, exist_ok=True)
    home = locations(target)
    with locked(home):
        if args.rollback:
            result = rollback(target, home)
        elif args.source:
            result = install(args.source.resolve(), target, home, args.apply, args.accept_existing)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                source, sha = fetch(Path(tmp))
                result = install(source, target, home, args.apply, args.accept_existing, sha)
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        raise SystemExit('更新未完成：' + str(exc))
