# SPDX-License-Identifier: MIT
"""Read launchd's embedded plist as data; no code disassembly or patching."""
from pathlib import Path, PurePosixPath
import plistlib
import struct


def configuration(binary):
    data=Path(binary).read_bytes()
    if len(data)<32 or struct.unpack_from('<III',data)!=(0xfeedfacf,0x100000c,0):
        raise ValueError('plain arm64 launchd required')
    ncmds,cmdbytes=struct.unpack_from('<II',data,16)
    end=32+cmdbytes
    if end>len(data):raise ValueError('truncated Mach-O commands')
    at=32;found=[]
    for _ in range(ncmds):
        if at+8>end:raise ValueError('truncated load command')
        cmd,size=struct.unpack_from('<II',data,at)
        if size<8 or at+size>end:raise ValueError('invalid load command size')
        if cmd==0x19:
            if size<72:raise ValueError('truncated segment')
            sections=struct.unpack_from('<I',data,at+64)[0]
            if 72+80*sections>size:raise ValueError('truncated sections')
            for j in range(sections):
                section=at+72+j*80
                if data[section:section+16].rstrip(b'\0')!=b'__config':continue
                length,offset=struct.unpack_from('<QI',data,section+40)
                if offset+length>len(data):raise ValueError('truncated config data')
                found.append(plistlib.loads(data[offset:offset+length].rstrip(b'\0')))
        at+=size
    if at!=end or len(found)!=1 or not isinstance(found[0].get('Boot'),dict):
        raise ValueError('one embedded Boot configuration required')
    return found[0]


def donor_files(binary,os_root):
    root=Path(os_root).resolve();files={};tasks={}
    for name,task in configuration(binary)['Boot'].items():
        program=task.get('Program')
        if program is None:
            args=task.get('ProgramArguments',[])
            if args and args[0].startswith('/'):program=args[0]
        if program is None:continue
        path=PurePosixPath(program)
        if not path.is_absolute() or '..' in path.parts:raise ValueError('invalid boot task path')
        source=root/str(path).lstrip('/')
        if not source.resolve().is_relative_to(root):
            raise ValueError('boot task path outside donor root')
        present=source.is_file()
        tasks[name]={'program':program,'present':present,'required_run':bool(task.get('RequireRun'))}
        if not present:
            if task.get('RequireRun'):raise ValueError('required boot task missing: '+program)
            continue
        files[program]=source
    return files,tasks
