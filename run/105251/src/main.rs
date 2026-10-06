fn test<'s: 's>(s: &'s str) -> impl std::future::Future<Output = impl Sized> {
    async move { let _s = s; }
}

fn main() {}