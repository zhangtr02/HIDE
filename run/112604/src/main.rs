trait Trait {
    type Gat<'lt>;
}

impl Trait for () {
    type Gat<'lt> = ();
}

fn dyn_hoops<T: Trait>(_: T) -> *const dyn FnOnce(T::Gat<'_>) {
    loop {}
}

fn main() {
    let _ = || { dyn_hoops(()) };
}