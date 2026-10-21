# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Execute actual native-probe reporting with host CF/MG/sysctl mocks."""
from pathlib import Path
import subprocess,os,tempfile,unittest
SOURCE=Path(__file__).resolve().parents[1]/'tools/mg-formfactor-probe.c'
class NativeFormFactorProbe(unittest.TestCase):
    def test_host_reporting_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            (out/'CoreFoundation').mkdir(exist_ok=True);(out/'sys').mkdir(exist_ok=True)
            (out/'CoreFoundation/CoreFoundation.h').write_text('''#pragma once
            #include <stddef.h>
            typedef const void *CFStringRef;
            #define CFSTR(x) ((CFStringRef)(x))
            #define kCFStringEncodingUTF8 0x08000100
            int CFStringGetCString(CFStringRef, char *, long, unsigned);
            void CFRelease(const void *);
            ''')
            (out/'sys/sysctl.h').write_text('''#pragma once
            #include <stddef.h>
            int sysctlbyname(const char *, void *, size_t *, void *, size_t);
            ''')
            (out/'mock.c').write_text(r'''
            #include <CoreFoundation/CoreFoundation.h>
            #include <stdlib.h>
            #include <string.h>
            #include <stdint.h>
            #include <stddef.h>
            static int is(const char *s) { const char *c=getenv("PROBE_CASE"); return c && !strcmp(c,s); }
            int sysctlbyname(const char *k, void *v, size_t *n, void *p, size_t z) {
                if (p || z || is("sysctl-failed")) return -1;
                const char *s = !strcmp(k,"hw.product") ? (is("old-kernel")?"Benesse TAB-A04-BR3":"iPad-A04BR3") :
                                !strcmp(k,"hw.machine") ? "Benesse TAB-A04-BR3" : "a04br3";
                size_t count=strlen(s)+1; if (*n<count) return -1;memcpy(v,s,count);*n=count;return 0;
            }
            void *dlopen(const char *p, int flags) {
                (void)flags;
                return is("missing-library") || strcmp(p,"/usr/lib/libMobileGestalt.dylib") ? NULL : (void *)1;
            }
            static CFStringRef mg_string(CFStringRef k) {
                if (!strcmp(k,"DeviceClass")) {
                    if(is("null-class"))return NULL;
                    return is("old-kernel") || is("stale-cache") ? "iPod" : "iPad";
                }
                return is("stale-cache") ? "Benesse TAB-A04-BR3" : "iPad-A04BR3";
            }
            static int32_t mg_int(CFStringRef k,int32_t fallback) {
                if(strcmp(k,"DeviceClassNumber"))return fallback;
                if(is("missing-number"))return fallback;
                return is("stale-cache") || is("old-kernel") ? 2 : 3;
            }
            void *dlsym(void *p, const char *s) {
                (void)p;
                if(is("missing-symbol"))return NULL;
                if(!strcmp(s,"MGGetStringAnswer"))return (void *)mg_string;
                if(!strcmp(s,"MGGetSInt32Answer"))return (void *)mg_int;
                return NULL;
            }
            int CFStringGetCString(CFStringRef s,char *p,long n,unsigned encoding) {
                (void)encoding;
                if(is("conversion-failed") || strlen(s)+1>(size_t)n)return 0;
                memcpy(p,s,strlen(s)+1);return 1;
            }
            void CFRelease(const void *p) { (void)p; }
            ''')
            program=out/'mg-formfactor-probe-host-mock'
            subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-I',str(out),str(SOURCE),str(out/'mock.c'),'-o',str(program)],check=True)
            cases={'tablet':0,'stale-cache':1,'old-kernel':1,'missing-library':2,'missing-symbol':2,'null-class':1,'missing-number':1,'conversion-failed':1,'sysctl-failed':1}
            results=[]
            for case,expected in cases.items():
             env=dict(os.environ,PROBE_CASE=case);r=subprocess.run([str(program)],env=env,text=True,capture_output=True)
             assert r.returncode==expected,(case,r.returncode,r.stdout,r.stderr)
             assert ('verdict=OBSERVED_TABLET' in r.stdout)==(case=='tablet'),(case,r.stdout)
             if case=='stale-cache':
              assert 'sysctl.hw.product=iPad-A04BR3' in r.stdout and 'MGGetStringAnswer.DeviceClass=iPod' in r.stdout
             results.append({'case':case,'exit':r.returncode,'stdout':r.stdout})
