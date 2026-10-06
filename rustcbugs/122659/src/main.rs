#![feature(core_intrinsics)]
#![feature(const_eval_select)]
use std::intrinsics::const_eval_select;
#[inline(always)]
pub const fn f() {
    const_eval_select((), g, g)
}
#[inline(always)]
pub const fn g() {
    const_eval_select((), f, f)
}

fn main() {}