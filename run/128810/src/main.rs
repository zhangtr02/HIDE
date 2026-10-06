#![feature(fn_delegation)]

use std::marker::PhantomData;

pub struct InvariantRef<'a, T: ?Sized>(&'a T, PhantomData<&'a mut &'a T>);

impl<'a> InvariantRef<'a, ()> {
    pub const NEW: Self = InvariantRef::new(&());
}

trait Trait {
    fn foo(&self) -> u8 { 0 }
}

struct Z(u8);

impl Trait for Z {
    reuse <u8 as Trait>::{foo} { &const { InvariantRef::<'a>::NEW } }
}

fn main() { }
