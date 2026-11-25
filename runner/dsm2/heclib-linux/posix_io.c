/*
 * POSIX replacements for HEClib v6's Windows file layer (heclib_c/*.c in the
 * Windows edition DWR ships in its DSM2 8.1.2 source package).
 *
 * Each function keeps the name and argument list the Fortran code calls.
 * Calling conventions, with Intel Fortran on Linux:
 *   - names are lower case with a trailing underscore;
 *   - every argument is passed by reference;
 *   - default INTEGER is 4 bytes. The Windows sources declare some of these
 *     as `long`, which is 4 bytes on Windows but 8 on Linux, so they are int32_t here;
 *   - CHARACTER arguments carry a hidden length (size_t) at the end of the list.
 */
#define _FILE_OFFSET_BITS 64
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

/* Copy a Fortran string to a C string: stop at a NUL or the declared length,
   then drop trailing blanks. */
static void fstr(char *dst, size_t cap, const char *src, size_t len)
{
    size_t n = 0;
    while (n < len && n + 1 < cap && src[n] != '\0') { dst[n] = src[n]; n++; }
    while (n > 0 && dst[n - 1] == ' ') n--;
    dst[n] = '\0';
}

/* iaccess 10: read/write; anything else: read only. Like Windows OPEN_ALWAYS,
   the file is created when it does not exist. istat: 0 ok, 1 error. */
void openf_(const char *cname, int32_t *iaccess, int32_t *ihandl, int32_t *istat, size_t len_cname)
{
    char path[4096];
    fstr(path, sizeof path, cname, len_cname);
    int flags = (*iaccess == 10) ? (O_RDWR | O_CREAT) : (O_RDONLY | O_CREAT);
    int fd = open(path, flags, 0666);
    *ihandl = fd;
    *istat = (fd >= 0) ? 0 : 1;
}

void closf_(int32_t *ihandle, int32_t *istat)
{
    *istat = (close(*ihandle) == 0) ? 0 : -1;
}

void readf_(int32_t *ihandl, void *buff, int32_t *nbytes, int32_t *istat, int32_t *ntrans)
{
    ssize_t n = read(*ihandl, buff, (size_t)*nbytes);
    *ntrans = (n >= 0) ? (int32_t)n : 0;
    *istat = (n >= 0) ? 0 : -1;
}

void writf_(int32_t *ihandl, void *buff, int32_t *nbytes, int32_t *istat, int32_t *ntrans)
{
    ssize_t n = write(*ihandl, buff, (size_t)*nbytes);
    *ntrans = (n >= 0) ? (int32_t)n : 0;
    *istat = (n >= 0) ? 0 : -1;
}

/* The Windows edition adds iorigin to the offset and seeks from the start of
   the file; this does the same. */
void seekf_(int32_t *ihandl, int32_t *iorigin, int32_t *iofset, int32_t *ipos, int32_t *istat)
{
    off_t want = (off_t)*iofset + (off_t)*iorigin;
    off_t got = lseek(*ihandl, want, SEEK_SET);
    *ipos = (int32_t)got;
    *istat = (got == want) ? 0 : -1;
}

static void seek64(int32_t *ihandl, int32_t *iorigin, int64_t *iofset, int32_t *ipos, int32_t *istat)
{
    off_t got = lseek(*ihandl, (off_t)*iofset, *iorigin);   /* 0, 1, 2 = SEEK_SET, SEEK_CUR, SEEK_END */
    *ipos = (int32_t)got;
    *istat = (got == (off_t)-1) ? -1 : 0;
}
void seek64_(int32_t *h, int32_t *o, int64_t *off, int32_t *p, int32_t *s)  { seek64(h, o, off, p, s); }
void seekc64_(int32_t *h, int32_t *o, int64_t *off, int32_t *p, int32_t *s) { seek64(h, o, off, p, s); }

void flushf_(int32_t *ihandle, int32_t *istat)
{
    *istat = (fsync(*ihandle) == 0) ? 0 : -1;
}

void filesize_(int32_t *ihandle, int32_t *isize)
{
    struct stat st;
    *isize = (fstat(*ihandle, &st) == 0) ? (int32_t)st.st_size : -1;
}

void filesizen_(const char *name, int32_t *length, int32_t *status, size_t n)
{
    char path[4096];
    struct stat st;
    fstr(path, sizeof path, name, n);
    *status = stat(path, &st);
    *length = (*status == 0) ? (int32_t)st.st_size : -1;
}

/* Record locks.
   mode 0: unlock.  1: lock, waiting up to 5 seconds (50 tries, 0.1 s apart, as on Windows).
   mode 2: lock, or return istat != 0 at once if already locked.  3: test only.
   istat: 0 ok, -1 failed or locked. */
static int setlock(int fd, short type, int32_t pos, int32_t len, int cmd)
{
    struct flock fl;
    memset(&fl, 0, sizeof fl);
    fl.l_type = type;
    fl.l_whence = SEEK_SET;
    fl.l_start = pos;
    fl.l_len = len;
    if (fcntl(fd, cmd, &fl) == 0) return 0;
    if (errno == EBADF && type == F_WRLCK) {          /* opened read-only: take a read lock */
        fl.l_type = F_RDLCK;
        return fcntl(fd, cmd, &fl);
    }
    return -1;
}

void lockdss_(int32_t *ihandle, int32_t *mode, int32_t *position, int32_t *nbytes, int32_t *istat)
{
    int fd = *ihandle, rc = -1;
    switch (*mode) {
    case 0:
        rc = setlock(fd, F_UNLCK, *position, *nbytes, F_SETLK);
        break;
    case 1:
        for (int i = 0; i < 50; i++) {
            if ((rc = setlock(fd, F_WRLCK, *position, *nbytes, F_SETLK)) == 0) break;
            usleep(100000);
        }
        break;
    case 2:
        rc = setlock(fd, F_WRLCK, *position, *nbytes, F_SETLK);
        break;
    case 3: {
        struct flock fl;
        memset(&fl, 0, sizeof fl);
        fl.l_type = F_WRLCK; fl.l_whence = SEEK_SET; fl.l_start = *position; fl.l_len = *nbytes;
        rc = (fcntl(fd, F_GETLK, &fl) == 0 && fl.l_type == F_UNLCK) ? 0 : -1;
        break;
    }
    }
    *istat = (rc == 0) ? 0 : -1;
}

/* Pause for secs seconds (replaces GEN/WAITS.FOR, which used Windows SLEEPQQ). */
void waits_(float *secs)
{
    if (*secs > 0) usleep((useconds_t)(*secs * 1e6f));
}

/* Every file the model runner uses is on a local disk. */
void isonlocaldrive_(const char *path, int32_t *isLocal, size_t len)
{
    (void)path; (void)len;
    *isLocal = 1;
}

void system_(const char *cmd, size_t len)
{
    char buf[4096];
    fstr(buf, sizeof buf, cmd, len);
    if (system(buf) == -1) perror("system");
}
