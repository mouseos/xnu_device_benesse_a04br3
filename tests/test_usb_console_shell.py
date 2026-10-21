# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Actual root console shell parser and real host filesystem, no target I/O."""
from pathlib import Path
import subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
class Shell(unittest.TestCase):
 def test_real_filesystem_output_errors_and_cached_request(self):
  code=r'''
#define A04_CONSOLE_TEST 1
#include "SOURCE"
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
int *__error(void){return &errno;}
int main(int argc,char **argv){assert(argc==2);assert(!chdir(argv[1]));char r[512];
 command("@A04 p shell pwd",r);assert(strstr(r,"OK shell-status=0"));
 command("@A04 l shell ls .",r);assert(strstr(r,"OK shell-status=0"));
 command("@A04 c shell cat payload",r);assert(strstr(r,"OK shell-status=0"));
 command("@A04 c shell cat payload",r);assert(strstr(r,"OK shell-status=0"));
 command("@A04 absent shell cat nonexistent",r);assert(strstr(r,"ERR"));
 command("@A04 cd shell cd sub",r);assert(strstr(r,"OK shell-status=0"));
 command("@A04 pwd shell pwd",r);assert(strstr(r,"OK shell-status=0"));
 command("@A04 invalid shell unknown",r);assert(strstr(r,"ERR 22"));
 command("@A04 help shell help",r);assert(strstr(r,"OK shell-status=0"));
 return 0;}
'''.replace('SOURCE',str(ROOT/'tools/uart-console.c'))
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'sub').mkdir();(p/'payload').write_bytes(b'UNIQUE\0BINARY\n')
   (p/'test.c').write_text(code)
   subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
   r=subprocess.run([str(p/'test'),d],capture_output=True,check=True,timeout=5)
   self.assertEqual(r.stdout.count(b'UNIQUE\0BINARY\n'),1)
   self.assertIn((d+'/sub').encode(),r.stdout)
   self.assertIn(b'payload\n',r.stdout)
if __name__=='__main__':unittest.main()
