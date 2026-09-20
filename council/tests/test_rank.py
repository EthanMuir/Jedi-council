from council.calibration.officer import SeatCalibration, rank_for_seat


def _calib(n, hit_rate):
    return SeatCalibration(
        seat_id="x", horizon=None, n_resolutions=n, hit_rate=hit_rate,
        brier_score=0.2, log_loss=0.5, calibration_curve=[],
    )


def test_below_floor_is_always_youngling_regardless_of_hit_rate():
    assert rank_for_seat(_calib(19, 0.9), min_resolutions=20) == "YOUNGLING"


def test_lucky_streak_cannot_mint_grand_master():
    # 5 resolutions, 100% hit rate -- still below the floor
    assert rank_for_seat(_calib(5, 1.0), min_resolutions=20) == "YOUNGLING"


def test_ranks_scale_with_hit_rate_above_floor():
    assert rank_for_seat(_calib(20, 0.70), min_resolutions=20) == "GRAND_MASTER"
    assert rank_for_seat(_calib(20, 0.60), min_resolutions=20) == "MASTER"
    assert rank_for_seat(_calib(20, 0.54), min_resolutions=20) == "KNIGHT"
    assert rank_for_seat(_calib(20, 0.45), min_resolutions=20) == "PADAWAN"


def test_no_resolutions_is_youngling():
    assert rank_for_seat(_calib(0, None), min_resolutions=20) == "YOUNGLING"
