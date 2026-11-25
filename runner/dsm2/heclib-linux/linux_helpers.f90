! Linux replacements for HEClib v6 routines whose Windows edition uses
! Windows-only Fortran modules (DFPORT, DFLIB, MSFLIB) or Windows paths.
! Same names and arguments as the routines they replace; standard Fortran only.

subroutine crenam(coldnm, cnewnm, ierr)
  ! Rename a file. ierr = 0 on success.
  character(len=*), intent(in) :: coldnm, cnewnm
  integer, intent(out) :: ierr
  call execute_command_line('mv -f -- "'//trim(coldnm)//'" "'//trim(cnewnm)//'"', exitstat=ierr)
end subroutine crenam

subroutine cdelet(cname, ierr)
  ! Delete a file. ierr = 0 on success, or if the file did not exist.
  character(len=*), intent(in) :: cname
  integer, intent(out) :: ierr
  integer :: u
  logical :: there
  ierr = 0
  inquire(file=trim(cname), exist=there)
  if (.not. there) return
  open(newunit=u, file=trim(cname), status='old', iostat=ierr)
  if (ierr == 0) close(u, status='delete', iostat=ierr)
end subroutine cdelet

subroutine cparms(c, m)
  ! Return the command-line arguments in c, separated by blanks, and their length in m.
  character(len=*), intent(out) :: c
  integer, intent(out) :: m
  character(len=1024) :: ct
  integer :: i, j
  c = ' '
  m = 0
  do i = 1, command_argument_count()
    call get_command_argument(i, ct)
    j = len_trim(ct)
    if (m + j + 1 > len(c)) exit
    if (m > 0) m = m + 1
    c(m + 1:m + j) = ct(1:j)
    m = m + j
  end do
end subroutine cparms

subroutine stdinc(cwait, cecho, cbreak, cflush, iascii, icode)
  ! Keyboard polling for interactive programs. The model runner has no keyboard:
  ! report "no key pressed".
  character(len=1), intent(in) :: cwait, cecho, cbreak, cflush
  integer(2), intent(out) :: iascii, icode
  iascii = -1
  icode = 0
end subroutine stdinc

subroutine tempname(cname, iunit)
  ! Name of a scratch file for a unit: $TMPDIR/scratch.NNN, or /tmp/scratch.NNN.
  character(len=*), intent(out) :: cname
  integer, intent(in) :: iunit
  character(len=1024) :: dir
  integer :: n, st
  call get_environment_variable('TMPDIR', dir, n, st)
  if (st /= 0 .or. n == 0) dir = '/tmp'
  write (cname, '(a,a,i3.3)') trim(dir), '/scratch.', iunit
end subroutine tempname

subroutine sortfilesfort(filein, fileout, status)
  ! Sort the lines of a catalogue file, as the GNU sort bundled with the
  ! Windows edition does. Uses the system's sort.
  character(len=*), intent(in) :: filein, fileout
  integer, intent(out) :: status
  call execute_command_line('LC_ALL=C sort -o "'//trim(fileout)//'" -- "'//trim(filein)//'"', exitstat=status)
end subroutine sortfilesfort
