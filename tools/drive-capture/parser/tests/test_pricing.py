"""TDD spec for the price normalisation helpers."""

from parser.pricing import promo_pct, to_cents


class TestToCents:
    def test_float(self):
        assert to_cents(7.39) == 739

    def test_int(self):
        assert to_cents(10) == 1000

    def test_float_rounding(self):
        # naive int(0.1*3*100) would give 29 — must round properly
        assert to_cents(0.29) == 29
        assert to_cents(2.155) == 216  # round half-up via Decimal

    def test_str_with_euro(self):
        assert to_cents("7,39 €") == 739

    def test_str_plain_comma(self):
        assert to_cents("7,39") == 739

    def test_str_with_dot(self):
        assert to_cents("7.39") == 739

    def test_str_per_measure(self):
        assert to_cents("10,61 € / kg") == 1061

    def test_str_per_measure_no_space(self):
        assert to_cents("28.42€/KG") == 2842

    def test_str_with_thin_spaces(self):
        assert to_cents("1 234,56 €") == 123456

    def test_empty_string(self):
        assert to_cents("") is None

    def test_whitespace_only(self):
        assert to_cents("   ") is None

    def test_none(self):
        assert to_cents(None) is None

    def test_garbage(self):
        assert to_cents("gratuit") is None

    def test_zero(self):
        assert to_cents(0) == 0
        assert to_cents("0,00 €") == 0


class TestPromoPct:
    def test_basic(self):
        # 1000 -> 800 = 20% off
        assert promo_pct(1000, 800) == 20

    def test_rounds(self):
        # 739 -> 599 = 18.94% -> 19
        assert promo_pct(739, 599) == 19

    def test_no_discount(self):
        assert promo_pct(1000, 1000) == 0

    def test_none_inputs(self):
        assert promo_pct(None, 800) is None
        assert promo_pct(1000, None) is None

    def test_zero_base(self):
        assert promo_pct(0, 0) is None

    def test_promo_above_base(self):
        # defensive: promo price higher than base -> not a discount
        assert promo_pct(800, 1000) is None
