use core::marker::PhantomData;

fn weird() -> PhantomData<impl Sized> {
    PhantomData
}

fn main() {}