# SPDX-License-Identifier: MIT
"""Generate an explicit service-stage cache from donor job descriptions."""
import copy
import json
from pathlib import Path, PurePosixPath
import plistlib


def prepare(os_root,profile_path,out):
    root=Path(os_root).resolve();out=Path(out);out.mkdir(parents=True,exist_ok=True)
    donor=plistlib.loads((root/'System/Library/xpc/launchd.plist').read_bytes())
    profile=json.loads(Path(profile_path).read_text())
    jobs=donor['LaunchDaemons'];selected={};files={};labels=set();manifest=[]
    for item in profile['services']:
        label=item['label']
        if label in labels:raise ValueError('duplicate service label')
        labels.add(label)
        matches=[(p,j) for p,j in jobs.items() if j.get('Label')==label]
        if len(matches)!=1:raise ValueError('service label not uniquely present in donor: '+label)
        guest,job=matches[0];job=copy.deepcopy(job)
        if set(item)-{'label','run_at_load'}:raise ValueError('unsupported service override')
        if 'run_at_load' in item:
            if not isinstance(item['run_at_load'],bool):raise ValueError('run_at_load must be boolean')
            job['RunAtLoad']=item['run_at_load']
        program=job.get('Program') or job.get('ProgramArguments',[''])[0]
        for name in (guest,program):
            path=PurePosixPath(name)
            if not path.is_absolute() or '..' in path.parts:raise ValueError('invalid daemon path')
        source=root/program.lstrip('/')
        if not source.resolve().is_relative_to(root) or not source.is_file():raise ValueError('donor executable missing or outside root')
        selected[guest]=job;files[program]=source
        jobfile=out/(label+'.plist');jobfile.write_bytes(plistlib.dumps(job))
        files[guest]=jobfile
        manifest.append({'label':label,'program':program,'run_at_load':job.get('RunAtLoad',False)})
    if not selected:raise ValueError('nonempty service profile required')
    cache={'VersionNumber':donor['VersionNumber'],'LaunchDaemons':selected,
        'AppExtensions':{},'AppRemovalServices':{},'SystemLibraryTreeState':{},'Symlinks':{}}
    path=out/'launchd.plist';path.write_bytes(plistlib.dumps(cache,fmt=plistlib.FMT_BINARY))
    files['/System/Library/xpc/launchd.plist']=path
    return files,manifest
