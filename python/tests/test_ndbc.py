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
