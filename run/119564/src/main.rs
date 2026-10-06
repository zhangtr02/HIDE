#![feature(coroutine_trait)]
#![feature(coroutines)]

use std::ops::Coroutine;

struct Invariant<'a>(fn(&'a ()) -> &'a ());

fn x<'a, 'b>() -> impl Coroutine<Invariant<'a>> {
    |_: Invariant<'a>| {
        let a: Invariant<'b> = yield ();
    }
}

fn main() {}