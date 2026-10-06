pub trait Fn0: Fn() -> Self::Out {
    type Out;
}

impl<F: Fn() -> ()> Fn0 for F {
    type Out = ();
}

pub fn closure_typer(_: impl Fn0) {}

fn main() {
    closure_typer(move || {});
}