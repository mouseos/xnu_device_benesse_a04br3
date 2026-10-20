# SPDX-License-Identifier: MIT
import json
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import launchd_profile

class LaunchdProfile(unittest.TestCase):
    def test_selection_preserves_job_and_rejects_unknown_or_missing_executable(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'System/Library/xpc').mkdir(parents=True);(root/'usr/sbin').mkdir(parents=True)
            (root/'usr/sbin/notifyd').write_bytes(b'donor executable')
            job={'Label':'com.apple.notifyd','ProgramArguments':['/usr/sbin/notifyd'],'MachServices':{'notify':True},'_PanicOnCrash':True}
            donor={'VersionNumber':7,'LaunchDaemons':{'/System/Library/LaunchDaemons/notify.plist':job,'/other':{'Label':'other'}}}
            (root/'System/Library/xpc/launchd.plist').write_bytes(plistlib.dumps(donor))
            profile=root/'profile.json';profile.write_text(json.dumps({'services':[{'label':'com.apple.notifyd','run_at_load':True}]}))
            files,services=launchd_profile.prepare(root,profile,root/'out')
            cache=plistlib.loads(files['/System/Library/xpc/launchd.plist'].read_bytes())
            self.assertEqual(len(cache['LaunchDaemons']),1)
            selected=next(iter(cache['LaunchDaemons'].values()))
            self.assertEqual(selected,{**job,'RunAtLoad':True})
            self.assertEqual(files['/usr/sbin/notifyd'].read_bytes(),b'donor executable')
            self.assertNotIn('RunAtLoad',plistlib.loads((root/'System/Library/xpc/launchd.plist').read_bytes())['LaunchDaemons']['/System/Library/LaunchDaemons/notify.plist'])
            (root/'usr/sbin/notifyd').unlink()
            with self.assertRaises(ValueError):launchd_profile.prepare(root,profile,root/'fail')
            profile.write_text(json.dumps({'services':[{'label':'unknown'}]}))
            with self.assertRaises(ValueError):launchd_profile.prepare(root,profile,root/'unknown')
