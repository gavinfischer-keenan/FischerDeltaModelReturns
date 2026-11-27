! dsstool: read and write regular-interval time series in HEC-DSS files, the
! format DSM2 reads its boundary inputs from (specification 6.1). A thin
! command-line wrapper on HEClib v6's ZRRTS and ZSRTS, so the model runner's
! Python code never links HEClib itself.
!
!   dsstool read  FILE PATH DATE TIME NVALS
!       prints "UNITS TYPE NVALS STATUS", then one value per line
!   dsstool write FILE PATH DATE TIME UNITS TYPE < values
!       stores one value per line from standard input
!   dsstool readir  FILE PATH JULS JULE
!       irregular series from day JULS to the end of day JULE (HEC day
!       numbers, 1 = 1 January 1900); prints "UNITS TYPE NVALS STATUS BASE",
!       then "MINUTES VALUE" per line, minutes after the start of day BASE
!   dsstool writeir FILE PATH UNITS TYPE BASE < "MINUTES VALUE" lines
!       stores an irregular series, replacing what the file holds over its span
!       (into a record it wrote itself: a record written in another form, such
!       as DWR's own gate records, is garbled by it, so delete that first)
!   dsstool delete FILE PATH
!       deletes one record, PATH with its D part (the block's start, such as
!       01JAN2020 for an IR-DECADE block); prints "deleted T" or "deleted F"
!
! PATH is a DSS pathname whose E part gives the interval, such as
! /FDMR/RSAC054/STAGE//15MIN/LIVE/. DATE and TIME are the first value's,
! such as 01NOV2026 0000. Missing values are -901.0, DSS's own marker.
program dsstool
  implicit none
  integer :: ifltab(1200), istat, nvals, iofset, i, ios, n, juls, jule, ibdate, kvals, m
  integer, allocatable :: itimes(:), gtimes(:)
  character(len=1024) :: cmd, fname, cpath, cdate, ctime, arg
  character(len=8) :: cunits, ctype
  real, allocatable :: values(:), grown(:)
  real :: v
  logical :: lfound

  call get_command_argument(1, cmd)
  call get_command_argument(2, fname)
  call get_command_argument(3, cpath)
  call get_command_argument(4, cdate)
  call get_command_argument(5, ctime)
  if (len_trim(cpath) == 0 .or. (len_trim(ctime) == 0 .and. trim(cmd) /= 'delete')) then
    write (0, '(a)') 'usage: dsstool read|write|readir|writeir|delete FILE PATH ...'
    stop 2
  end if

  call zset('MLEVEL', ' ', 0)  ! no chatter on standard output
  call zopen(ifltab, trim(fname), istat)
  if (istat /= 0) then
    write (0, '(a,i0)') 'cannot open DSS file, status ', istat
    stop 3
  end if

  select case (trim(cmd))
  case ('read')
    call get_command_argument(6, arg)
    read (arg, *) nvals
    allocate (values(nvals))
    call zrrts(ifltab, trim(cpath), trim(cdate), trim(ctime), nvals, values, cunits, ctype, iofset, istat)
    write (*, '(a,1x,a,1x,i0,1x,i0)') trim(cunits), trim(ctype), nvals, istat
    do i = 1, nvals
      write (*, '(g0)') values(i)
    end do
  case ('write')
    call get_command_argument(6, arg)
    cunits = arg
    call get_command_argument(7, arg)
    ctype = arg
    allocate (values(1024))
    n = 0
    do
      read (*, *, iostat=ios) v
      if (ios /= 0) exit
      if (n == size(values)) then
        allocate (grown(2 * n))
        grown(1:n) = values
        call move_alloc(grown, values)
      end if
      n = n + 1
      values(n) = v
    end do
    call zsrts(ifltab, trim(cpath), trim(cdate), trim(ctime), n, values, cunits, ctype, 0, istat)
    if (istat /= 0) then
      write (0, '(a,i0)') 'write failed, status ', istat
      call zclose(ifltab)
      stop 4
    end if
    write (*, '(a,i0)') 'stored ', n
  case ('readir')
    read (cdate, *) juls
    read (ctime, *) jule
    kvals = 200000
    allocate (values(kvals), itimes(kvals))
    call zrits(ifltab, trim(cpath), juls, 0, jule, 1440, itimes, values, kvals, nvals, ibdate, cunits, ctype, istat)
    write (*, '(a,1x,a,1x,i0,1x,i0,1x,i0)') trim(cunits), trim(ctype), nvals, istat, ibdate
    do i = 1, nvals
      write (*, '(i0,1x,g0)') itimes(i), values(i)
    end do
  case ('writeir')
    cunits = cdate
    ctype = ctime
    call get_command_argument(6, arg)
    read (arg, *) ibdate
    allocate (values(1024), itimes(1024))
    n = 0
    do
      read (*, *, iostat=ios) m, v
      if (ios /= 0) exit
      if (n == size(values)) then
        allocate (grown(2 * n), gtimes(2 * n))
        grown(1:n) = values
        gtimes(1:n) = itimes
        call move_alloc(grown, values)
        call move_alloc(gtimes, itimes)
      end if
      n = n + 1
      itimes(n) = m
      values(n) = v
    end do
    call zsits(ifltab, trim(cpath), itimes, values, n, ibdate, cunits, ctype, 1, istat)
    if (istat /= 0) then
      write (0, '(a,i0)') 'irregular write failed, status ', istat
      call zclose(ifltab)
      stop 4
    end if
    write (*, '(a,i0)') 'stored ', n
  case ('delete')
    call zdelet(ifltab, trim(cpath), len_trim(cpath), lfound)
    write (*, '(a,l1)') 'deleted ', lfound
  case default
    write (0, '(a)') 'unknown command: '//trim(cmd)
    call zclose(ifltab)
    stop 2
  end select
  call zclose(ifltab)
end program dsstool
