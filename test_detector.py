import unittest

from detector import RowSnapshot, detect_dov_status


class DovDetectorTests(unittest.TestCase):
    def test_screenshot_state_is_unavailable_even_with_external_link(self):
        rows = [
            RowSnapshot(
                row_text=(
                    "CONSULAR SERVICES Consular services DOV only for universities "
                    "Booking calendar not yet available Link 1"
                ),
                booking_text="Booking calendar not yet available",
                # Link 1 is outside the Booking cell, so the monitor must not report it.
                has_enabled_booking_control=False,
            )
        ]
        self.assertEqual(detect_dov_status(rows).status, "unavailable")

    def test_english_book_button_is_available(self):
        rows = [
            RowSnapshot(
                row_text="CONSULAR SERVICES DOV only for universities BOOK",
                booking_text="BOOK",
                has_booking_control=True,
                has_enabled_booking_control=True,
                booking_control_text="BOOK",
            )
        ]
        self.assertEqual(detect_dov_status(rows).status, "available")

    def test_italian_prenota_button_is_available(self):
        rows = [
            RowSnapshot(
                row_text="SERVIZI CONSOLARI DOV solo per università PRENOTA",
                booking_text="PRENOTA",
                has_booking_control=True,
                has_enabled_booking_control=True,
                booking_control_text="PRENOTA",
            )
        ]
        self.assertEqual(detect_dov_status(rows).status, "available")

    def test_italian_unavailable_text(self):
        rows = [
            RowSnapshot(
                row_text=(
                    "SERVIZI CONSOLARI DOV esclusivamente per le università "
                    "Calendario prenotazioni non ancora disponibile"
                ),
                booking_text="Calendario prenotazioni non ancora disponibile",
            )
        ]
        self.assertEqual(detect_dov_status(rows).status, "unavailable")

    def test_generic_declaration_of_value_is_not_target(self):
        rows = [
            RowSnapshot(
                row_text="CONSULAR SERVICES Declaration of value BOOK",
                booking_text="BOOK",
                has_booking_control=True,
                has_enabled_booking_control=True,
                booking_control_text="BOOK",
            )
        ]
        self.assertEqual(detect_dov_status(rows).status, "not_found")

    def test_unfamiliar_target_cell_does_not_false_alert(self):
        rows = [
            RowSnapshot(
                row_text="CONSULAR SERVICES DOV only for universities Please wait",
                booking_text="Please wait",
            )
        ]
        self.assertEqual(detect_dov_status(rows).status, "unknown")

    def test_disabled_book_control_does_not_false_alert(self):
        rows = [
            RowSnapshot(
                row_text="CONSULAR SERVICES DOV only for universities BOOK",
                booking_text="BOOK",
                has_booking_control=True,
                has_enabled_booking_control=False,
                booking_control_text="BOOK",
            )
        ]
        self.assertEqual(detect_dov_status(rows).status, "unknown")


if __name__ == "__main__":
    unittest.main()
