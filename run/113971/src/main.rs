#![feature(type_alias_impl_trait)]
trait Captures<'a> {}
impl<T> Captures<'_> for T {}

fn ensure_outlives<'a, X: 'a>(_: X) {}
fn relate<X>(_: X, _: X) {}

type Opaque<'a> = impl Copy + Captures<'a>;
fn test<'x>(_: Opaque<'x>) {
    let opaque = None::<Opaque<'_>>; // let's call this lifetime '?1
    let hidden = None::<u8>;
    ensure_outlives::<'x>(opaque); // outlives constraint: '?1: 'x
    relate(opaque, hidden); // defining use: Opaque<'?1> := u8
}

fn main() {}