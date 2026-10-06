trait Range {
    const FIRST: u8;
    const LAST: u8;
}

struct TwoDigits;
impl Range for TwoDigits {
    const FIRST:  = 10;
    const LAST: u8 = 99;
}

fn digits(x: u8) -> u32 {
    match x {
        TwoDigits::FIRST..=TwoDigits::LAST => 2,
    }
}

fn main() {}