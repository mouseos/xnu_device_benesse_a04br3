# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Build-script command/failure contract with fake tools; no SDK/signature claim."""
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import unittest

TOOLS=Path(__file__).resolve().parents[1]/'tools'
SCRIPT=TOOLS/'build-a04br3-keyboard-state.sh'

class BuildReader(unittest.TestCase):
    def test_source_built_sign_verify_and_failures_preserve_output(self):
        subprocess.run(['bash','-n',str(SCRIPT)],check=True)
        for failure in ('none','compile','sign','verify'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as d:
                p=Path(d);binpath=p/'bin';binpath.mkdir()
                log=p/'calls.jsonl'
                # Explicit test doubles: record commands, never compile/sign a real payload.
                xcrun=binpath/'xcrun'
                xcrun.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
with open(os.environ['TOOL_LOG'],'a') as f:f.write(json.dumps(['xcrun']+sys.argv[1:])+'\\n')
if sys.argv[1:]==['--sdk','iphoneos','--show-sdk-path']:print('/mock SDK');sys.exit(0)
assert sys.argv[1:4]==['--sdk','iphoneos','clang']
if os.environ['TOOL_FAIL']=='compile':sys.exit(1)
Path(sys.argv[sys.argv.index('-o')+1]).write_bytes(b'fake compiler output, not signed Mach-O')
''')
                signer=binpath/'codesign'
                signer.write_text('''#!/usr/bin/env python3
import json,os,sys
with open(os.environ['TOOL_LOG'],'a') as f:f.write(json.dumps(['codesign']+sys.argv[1:])+'\\n')
step='verify' if '--verify' in sys.argv else 'sign'
sys.exit(1 if os.environ['TOOL_FAIL']==step else 0)
''')
                xcrun.chmod(0o755);signer.chmod(0o755)
                out=p/'output path'/'reader';out.parent.mkdir();out.write_bytes(b'previous binary')
                env=dict(os.environ,PATH=str(binpath)+os.pathsep+os.environ['PATH'],TOOL_LOG=str(log),TOOL_FAIL=failure)
                result=subprocess.run(['bash',str(SCRIPT),str(out)],env=env,capture_output=True)
                calls=[json.loads(line) for line in log.read_text().splitlines()]
                if failure=='none':
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertEqual(calls[-2][0:6],['codesign','--force','--sign','-','--timestamp=none',calls[-2][-1]])
                    self.assertEqual(calls[-1][0:3],['codesign','--verify','--strict'])
                    self.assertEqual(calls[-1][-1],calls[-2][-1])
                    compile=calls[1];self.assertIn('arm64-apple-ios16.0',compile)
                    self.assertIn(str(TOOLS/'a04br3_keyboard_state.c'),compile)
                    self.assertNotIn('--entitlements',str(calls))
                    self.assertIn('a04br3_keyboard_state_sources.json',Path(str(out)+'.sha256').read_text())
                else:
                    self.assertNotEqual(result.returncode,0)
                    self.assertEqual(out.read_bytes(),b'previous binary')
                    self.assertFalse(Path(str(out)+'.sha256').exists())
                self.assertEqual(list(out.parent.glob('reader.build.*')),[])

    def test_provenance_and_fixed_target_contract(self):
        meta=json.loads((TOOLS/'a04br3_keyboard_state_sources.json').read_text())
        self.assertEqual(meta['target']['uuid'],'1242978a2c2c37818d6c9777edce2804')
        self.assertEqual(len(meta['target']['original_sha256']),64)
        self.assertEqual(set(meta['build']['payload_group']),{
            'tools/a04br3_keyboard_state.c','tools/a04br3_keyboard_state_sources.json',
            'tools/build-a04br3-keyboard-state.sh'})
        self.assertTrue(all((TOOLS.parent/path).is_file() for path in meta['build']['payload_group']))
        source=(TOOLS/'a04br3_keyboard_state.c').read_text()
        uuid=re.search(r'keyboard_uuid\[16\] = \{(.*?)\}',source,re.S).group(1)
        self.assertEqual(bytes(int(x,16) for x in re.findall(r'0x([0-9a-f]{2})',uuid)).hex(),meta['target']['uuid'])
        original={entry['name']:entry for entry in meta['remote_ivar_types']}
        labels={'remoteConnection':'_connection','remoteExpectingInitialState':'_expectingInitialState',
                'remoteCurrentKeyboard':'_currentKeyboard','remoteCurrentState':'_currentState',
                'remoteExpectedSuppression':'_expectedSuppression','remoteExternalSuppression':'_externalSuppression',
                'remoteRequiredScene':'_requiredScene','remoteSuppressedScene':'_suppressedScene',
                'remoteWindowEnabled':'_windowEnabled'}
        offsets={entry['name'].split('.')[-1]:entry['offset'] for entry in meta['remote_ivars']}
        for label,offset,width in re.findall(r'keyboard_object\("([^"]+)", remote, (0x[0-9a-f]+), ([0-9]+)\)',source):
            name=labels[label]
            self.assertEqual(int(offset,16),offsets[name])
            self.assertEqual(int(width),original[name]['bytes'])
        self.assertEqual(len(labels),len(re.findall(r'keyboard_object\("[^"]+", remote,',source)))


if __name__=='__main__':unittest.main()
