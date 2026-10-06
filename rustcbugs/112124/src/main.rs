#![feature(adt_const_params)]

use core::marker::ConstParamTy;

struct Foo;

impl ConstParamTy for &'static Foo {}

fn main() {}