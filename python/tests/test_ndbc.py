from fdmr_adapters import ndbc


def test_ocean_file_rows_become_temperature_and_salinity_oldest_first():
    lines = [
        "#YY  MM DD hh mm   DEPTH  OTMP   COND   SAL   O2% O2PPM  CLCON  TURB    PH    EH",
        "#yr  mo dy hr mn       m  degC  mS/cm   psu     %   ppm   ug/l   FTU     -    mv",
        "2026 11 26 07 30     1.4 12.80  35.91 22.70  90.5  8.30     MM   424  7.60    MM",
        "2026 11 26 07 15     1.3    MM  35.73 22.60  91.6  8.40     MM   319  7.60    MM",
    ]
    got = ndbc.parse(lines)
    assert [v.value for v in got["salinity"]] == [22.6, 22.7]
    assert [v.value for v in got["water_temperature"]] == [12.8]  # the missing one is skipped
    assert got["salinity"][0].t < got["salinity"][1].t


def test_overlay_fetch_asks_cdec_only_for_its_own_sensors():
    from fdmr_adapters import cdec
    from fdmr_runner import overlays

    groups = overlays.fetch_groups({"stations": [{"site": "ABC"}, {"site": "SCQC1", "source": "ndbc"}]})
    assert groups and all(p in cdec.SENSORS for _, _, ps in groups for p in ps)
    assert all("SCQC1" not in sites for _, sites, _ in groups)


def test_standard_file_gives_a_buoys_sea_temperature():
    lines = ["#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE",
             "#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft",
             "2026 11 26 08 30  MM   MM   MM   1.2    14   7.9 270     MM    MM  17.1    MM   MM   MM    MM",
             "2026 11 26 08 00  MM   MM   MM   1.3    14   8.0 271     MM    MM    MM    MM   MM   MM    MM"]
    got = ndbc.parse(lines, ndbc.MET_COLUMNS)
    assert [v.value for v in got["water_temperature"]] == [17.1]
    assert got["air_temperature"] == []
