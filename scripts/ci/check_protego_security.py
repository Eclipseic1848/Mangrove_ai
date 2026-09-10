"""Protego安全升级回归：恶意通配匹配有界，正常robots语义保留。"""
from importlib.metadata import version
import json
import subprocess
import sys

script = r'''
from protego import Protego
import time,json
start=time.perf_counter()
rules=Protego.parse('User-agent: *\nDisallow: /'+'*1'*12+'*Z\n')
assert rules.can_fetch('/'+'1'*60,'mybot')
regular=Protego.parse('User-agent: *\nDisallow: /private/\nAllow: /private/public/\nDisallow: /*.pdf$\n')
assert not regular.can_fetch('https://example.org/private/a','mybot')
assert regular.can_fetch('https://example.org/private/public/a','mybot')
assert not regular.can_fetch('https://example.org/file.pdf','mybot')
assert regular.can_fetch('https://example.org/file.pdf?preview=1','mybot')
print(json.dumps({'seconds':time.perf_counter()-start,'red_os':'bounded','normal_rules':'passed'}))
'''
if __name__ == '__main__':
    assert version('protego') == '0.6.2'
    # 旧版本若出现指数回溯，父进程会终止子进程，不遗留占用。
    result=subprocess.run([sys.executable,'-X','utf8','-c',script],capture_output=True,text=True,encoding='utf-8',timeout=5,check=True)
    print(result.stdout.strip())
