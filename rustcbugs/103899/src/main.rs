use std::collections::HashMap;
use std::marker::PhantomData;
use std::sync::Arc;

trait DataTypePrimitive {
    type DataTypeHolder;
}

trait LayerT {
    type DataType: DataTypePrimitive;
}

struct Pooled<B> {
    _buffer: PhantomData<B>,
}

struct NdBufferView<B, T: DataTypePrimitive> {
    buffer: B,
    data_type_h: T::DataTypeHolder,
}

type NdBuffer<T> = NdBufferView<(), T>;

type PooledNdBuffer<T> = Pooled<NdBuffer<T>>;

struct LayerPage<L: LayerT> {
    // NOTE! ICE if this is PooledNdBuffer
    buffer: Arc<PooledNdBuffer<L::DataType>>,
    // These work fine:
    //buffer: Arc<Pooled<Vec<L::DataType>>>,
    //buffer: Arc<Vec<L::DataType>>,
}

type Layer<L> = HashMap<u32, LayerPage<L>>;

// This function triggers the ICE.
fn layer_from_proto<T, L: LayerT<DataType = T>>(
    _layer: &'static L,
) -> Result<Layer<L>, ()> {
    todo!()
}

fn main() {}