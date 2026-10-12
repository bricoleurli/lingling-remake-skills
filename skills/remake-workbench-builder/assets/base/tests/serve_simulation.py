"""Temporary browser QA server. Only local simulated providers; never use as user app."""
from test_workbench import WorkbenchTest
from server import Server
case=WorkbenchTest();case.setUp()
try:
    job=case.plan();server=Server(('127.0.0.1',8767),case.store)
    print('仅模拟测试 http://127.0.0.1:8767，任务 '+job['id'],flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
finally:case.tearDown()
