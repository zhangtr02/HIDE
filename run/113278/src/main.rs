#![feature(trivial_bounds, type_alias_impl_trait)]
mod sus {
    pub type Sep = impl Sized;
    pub fn define_sep() -> Sep {
        String::new()
    }

    pub type Tait = impl Mk;

    pub trait Mk: Proj {
        fn mk() -> <Self as Proj>::Assoc;
    }

    impl<T> Mk for T
    where
        T: Proj<Assoc = ()>,
    {
        fn mk() -> <T as Proj>::Assoc {
            ()
        }
    }

    pub trait Proj {
        type Assoc;
    }
    impl Proj for () {
        type Assoc = Sep;
    }

    fn define() -> Tait
    where
        (): Proj<Assoc = ()>,
    {
    }
}

fn main() {
    let _ = <sus::Tait as sus::Mk>::mk();
}