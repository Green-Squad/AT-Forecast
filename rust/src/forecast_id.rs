//! Preserve imported Rails IDs while exposing native forecasts to legacy clients.
//!
//! The installed Android app uses Java Integer for both forecast IDs. Native
//! storage IDs have a separate 64-bit namespace; project that namespace into a
//! stable positive signed-32-bit range on the wire, without changing stored IDs.
pub const NATIVE_START: i64 = 4_294_967_296;
const PUBLIC_START: i64 = 1_000_000_000;

pub fn public_id(stored: i64) -> Option<i32> {
    let public = if stored >= NATIVE_START {
        stored
            .checked_sub(NATIVE_START)?
            .checked_add(PUBLIC_START)?
    } else {
        stored
    };
    i32::try_from(public).ok().filter(|id| *id > 0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn imported_ids_are_unchanged_and_native_ids_fit_android_integers() {
        assert_eq!(public_id(42), Some(42));
        assert_eq!(public_id(83_000_000), Some(83_000_000));
        assert_eq!(public_id(NATIVE_START), Some(1_000_000_000));
        assert_eq!(public_id(NATIVE_START + 13_213), Some(1_000_013_213));
        let last = NATIVE_START + i64::from(i32::MAX) - PUBLIC_START;
        assert_eq!(public_id(last), Some(i32::MAX));
        assert_eq!(public_id(last + 1), None);
        assert_eq!(public_id(i64::MAX), None);
        assert_eq!(public_id(0), None);
        assert_eq!(public_id(-1), None);
    }
}
