//! The public API and stored forecasts share the Rails signed-32-bit ID contract.
//! Each weather table continues its own original sequence; never remap IDs.
pub fn public_id(stored: i64) -> Option<i32> {
    i32::try_from(stored).ok().filter(|id| *id > 0)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn forecast_ids_are_unchanged_and_checked_for_android() {
        for id in [1, 1_428_139, 8_160_071, i32::MAX] {
            assert_eq!(public_id(i64::from(id)), Some(id));
        }
        assert_eq!(public_id(i64::from(i32::MAX) + 1), None);
        assert_eq!(public_id(4_294_967_296), None);
        assert_eq!(public_id(0), None);
        assert_eq!(public_id(-1), None);
    }
}
