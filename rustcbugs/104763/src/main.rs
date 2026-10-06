trait Trait {
    const TRAIT: bool;
}

impl<T> Trait for &'static T {
    const TRAIT: bool = true;
}

fn test<T>() {
    <&'static T>::TRAIT;
}

fn main() {}