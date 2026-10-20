/* SPDX-License-Identifier: MIT
 * Read-only range files over one immutable image. No mtkclient modification.
 */
#define FUSE_USE_VERSION 26
#define _FILE_OFFSET_BITS 64
#include <fuse.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
struct range { uint64_t start,length; char name[64]; };
static struct range *ranges;
static unsigned count;
static int image;
static struct range *lookup(const char *path) {
    if (path[0]!='/') return NULL;
    for (unsigned i=0;i<count;i++) if (!strcmp(path+1,ranges[i].name)) return &ranges[i];
    return NULL;
}
static int attributes(const char *path,struct stat *st) {
    memset(st,0,sizeof(*st));st->st_uid=getuid();st->st_gid=getgid();
    if (!strcmp(path,"/")) { st->st_mode=S_IFDIR|0555;st->st_nlink=2;return 0; }
    struct range *r=lookup(path);if (!r) return -ENOENT;
    st->st_mode=S_IFREG|0444;st->st_nlink=1;st->st_size=(off_t)r->length;
    st->st_blksize=4096;st->st_blocks=(r->length+511)/512;return 0;
}
static int directory(const char *path,void *buffer,fuse_fill_dir_t fill,off_t offset,struct fuse_file_info *fi) {
    (void)fi;if (strcmp(path,"/")) return -ENOENT;
    if (offset<0) return -EINVAL;
    for (uint64_t i=(uint64_t)offset;i<(uint64_t)count+2;i++) {
        const char *name=i==0 ? "." : (i==1 ? ".." : ranges[i-2].name);
        if (fill(buffer,name,NULL,(off_t)(i+1))) break;
    }
    return 0;
}
static int open_file(const char *path,struct fuse_file_info *fi) {
    if ((fi->flags&O_ACCMODE)!=O_RDONLY) return -EROFS;
    return lookup(path) ? 0 : -ENOENT;
}
static int read_file(const char *path,char *buffer,size_t length,off_t offset,struct fuse_file_info *fi) {
    (void)fi;struct range *r=lookup(path);if (!r) return -ENOENT;
    if (offset<0) return -EINVAL;
    if ((uint64_t)offset>=r->length) return 0;
    if (length>r->length-(uint64_t)offset) length=(size_t)(r->length-(uint64_t)offset);
    ssize_t result=pread(image,buffer,length,(off_t)(r->start+(uint64_t)offset));
    return result<0 ? -errno : (int)result;
}
int main(int argc,char **argv) {
    if (argc!=4) { fprintf(stderr,"usage: range-view IMAGE RANGES.tsv MOUNTPOINT\n");return 2; }
    image=open(argv[1],O_RDONLY);struct stat st;
    if (image<0 || fstat(image,&st) || !S_ISREG(st.st_mode)) return 2;
    FILE *input=fopen(argv[2],"r");if (!input) return 2;
    ranges=calloc(65536,sizeof(*ranges));if (!ranges) return 2;
    unsigned long long start,length;char name[64];
    char line[256],extra;
    while (fgets(line,sizeof(line),input)) {
        if (sscanf(line,"%llx %llx %63s %c",&start,&length,name,&extra)!=3) return 2;
        if (count==65536 || !length || start>(uint64_t)st.st_size || length>(uint64_t)st.st_size-start ||
            strchr(name,'/') || !strcmp(name,".") || !strcmp(name,"..")) return 2;
        for (unsigned i=0;i<count;i++) if (!strcmp(name,ranges[i].name)) return 2;
        ranges[count].start=start;ranges[count].length=length;strcpy(ranges[count].name,name);count++;
    }
    if (!feof(input) || !count) return 2;
    fclose(input);
    struct fuse_operations operations={.getattr=attributes,.readdir=directory,.open=open_file,.read=read_file};
    char *args[]={argv[0],argv[3],"-oro,default_permissions,fsname=xnu-image-ranges"};
    return fuse_main(3,args,&operations,NULL);
}
