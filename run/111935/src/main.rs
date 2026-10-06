#![feature(type_alias_impl_trait)]

type Opaque<T> = impl Sized;
fn foo<T>() -> Opaque<T> {
    let _: () = foo::<u8>();
    //~^ ERROR expected generic type parameter, found `u8`
}

fn main() {}