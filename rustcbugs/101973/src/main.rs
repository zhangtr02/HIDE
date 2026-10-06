#[inline]
pub fn imm8(x: u32) -> u32 {
    let mut out = 0u32;
    out |= (x >> 0) & 0xff;
    out
}

#[inline(never)]
pub fn inner(fields: u32) -> i64 {
    imm8(fields).rotate_right(((fields >> 8) & 0xf) << 1) as i32 as i64
}

fn main() {
    let val = inner(0xe32cf20f);
    println!("{val:#x}");
}