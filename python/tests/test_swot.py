from fdmr_adapters import swot


def test_hydrocron_csv_keeps_good_and_suspect_passes_and_drops_fill():
    csv = "reach_id,time_str,wse,width,reach_q,wse_units,width_units\n" \
          "1,2026-10-01T11:35:19Z,7.0,45.7,1,m,m\n" \
          "1,2026-10-02T00:49:47Z,6.5,51.5,2,m,m\n" \
          "1,2026-10-03T00:00:00Z,-999999999999,60.0,0,m,m\n" \
          "1,no_data,-999999999999,-999999999999,3,m,m\n"
    got = swot.parse(csv)
    assert [v.value for v in got["width"]] == [45.7, 60.0]
    assert [v.value for v in got["wse"]] == [7.0]
