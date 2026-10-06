#![feature(type_alias_impl_trait)]

mod lifetime_params {
    type Ty<'a> = impl Sized + 'a;
    fn define(s: &str) -> Ty<'_> { s }

    type BadFnSig = fn(Ty<'_>) -> &str;
    type BadTraitRef = dyn Fn(Ty<'_>) -> &str;
}

mod type_params {
    type Ty<T> = impl Sized;
    fn define<T>(s: T) -> Ty<T> { s }

    type BadFnSig = fn(Ty<&str>) -> &str;
    type BadTraitRef = dyn Fn(Ty<&str>) -> &str;
}

fn main() {}