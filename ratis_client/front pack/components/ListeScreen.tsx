import { useState } from 'react';
import { Plus, ChevronDown, Navigation, GripVertical } from 'lucide-react';
import { DndProvider, useDrag, useDrop } from 'react-dnd';
import { HTML5Backend } from 'react-dnd-html5-backend';
import { PaperPoster } from './PaperPoster';

interface Product {
  id: number;
  name: string;
  price: number;
  quantity: number;
}

interface Store {
  id: number;
  name: string;
  address: string;
  products: Product[];
  position: { x: number; y: number };
  color: string;
}

interface DraggableProductProps {
  product: Product;
  storeId: number;
}

const DraggableProduct = ({ product, storeId }: DraggableProductProps) => {
  const [{ isDragging }, drag] = useDrag(() => ({
    type: 'PRODUCT',
    item: { productId: product.id, fromStoreId: storeId },
    collect: (monitor) => ({
      isDragging: !!monitor.isDragging(),
    }),
  }));

  return (
    <div
      ref={drag}
      className="flex items-center gap-2 py-2 px-3 rounded-lg cursor-move"
      style={{ 
        background: 'rgba(42, 157, 143, 0.15)',
        opacity: isDragging ? 0.5 : 1,
        transition: 'opacity 0.2s'
      }}
    >
      <GripVertical className="w-4 h-4 flex-shrink-0" style={{ color: '#5A5A5A' }} />
      <div className="flex-1">
        <div className="text-[13px]" style={{ color: '#2A2A2A' }}>
          {product.name}
        </div>
        <div className="text-[11px] mt-0.5" style={{ color: '#5A5A5A' }}>
          Qté: {product.quantity}
        </div>
      </div>
      <div className="text-[#2A2A2A] text-[14px] font-bold">
        {(product.price * product.quantity).toFixed(2)}€
      </div>
    </div>
  );
};

interface StoreAccordionProps {
  store: Store;
  index: number;
  isOpen: boolean;
  onToggle: () => void;
  onDrop: (productId: number, fromStoreId: number, toStoreId: number) => void;
}

const StoreAccordion = ({ store, index, isOpen, onToggle, onDrop }: StoreAccordionProps) => {
  const [{ isOver }, drop] = useDrop(() => ({
    accept: 'PRODUCT',
    drop: (item: { productId: number; fromStoreId: number }) => {
      if (item.fromStoreId !== store.id) {
        onDrop(item.productId, item.fromStoreId, store.id);
      }
    },
    collect: (monitor) => ({
      isOver: !!monitor.isOver(),
    }),
  }));

  const getTotalForStore = (products: Product[]) => {
    return products.reduce((sum, product) => sum + (product.price * product.quantity), 0);
  };

  const storeTotal = getTotalForStore(store.products);

  return (
    <div ref={drop}>
      <PaperPoster 
        rotation={index % 3 === 0 ? 0.4 : index % 3 === 1 ? -0.4 : 0.2}
        size="md"
      >
        <div 
          className="relative"
          style={{
            border: isOver ? `2px solid ${store.color}` : 'none',
            borderRadius: '4px',
            transition: 'all 0.2s'
          }}
        >
          {/* Accordion Header */}
          <button
            onClick={onToggle}
            className="w-full flex items-center justify-between relative z-10"
          >
            <div className="flex items-center gap-3">
              {/* Store Number Badge */}
              <div 
                className="w-8 h-8 rounded-full flex items-center justify-center font-bold text-[14px]"
                style={{ 
                  background: store.color,
                  color: '#ffffff',
                  boxShadow: `0 2px 4px ${store.color}60`
                }}
              >
                {index + 1}
              </div>

              {/* Store Info */}
              <div className="text-left">
                <div className="text-[#2A2A2A] text-[15px] font-bold">
                  {store.name}
                </div>
                <div className="text-[11px]" style={{ color: '#5A5A5A' }}>
                  {store.products.length} produits • {storeTotal.toFixed(2)}€
                </div>
              </div>
            </div>

            <div className="flex items-center gap-2">
              {/* Plus Button */}
              <div 
                className="w-8 h-8 rounded-full flex items-center justify-center"
                style={{ 
                  background: 'rgba(42, 157, 143, 0.2)',
                  border: '1px solid rgba(42, 157, 143, 0.4)'
                }}
                onClick={(e) => {
                  e.stopPropagation();
                  // Handle add product
                }}
              >
                <Plus className="w-4 h-4" style={{ 
                  color: '#2A9D8F',
                  filter: 'drop-shadow(0 0 2px rgba(0,0,0,0.6)) drop-shadow(0 0 3px rgba(0,0,0,0.5))'
                }} />
              </div>

              {/* Chevron */}
              <ChevronDown 
                className="w-5 h-5 transition-transform duration-200"
                style={{ 
                  color: '#5A5A5A',
                  transform: isOpen ? 'rotate(180deg)' : 'rotate(0deg)'
                }}
              />
            </div>
          </button>

          {/* Accordion Content */}
          {isOpen && (
            <div 
              className="relative z-10 mt-3"
              style={{ 
                borderTop: '1px solid rgba(0, 0, 0, 0.1)',
                paddingTop: '12px'
              }}
            >
              <div className="space-y-2">
                {store.products.length === 0 ? (
                  <div 
                    className="py-6 text-center rounded-lg border-2 border-dashed"
                    style={{ 
                      borderColor: 'rgba(0, 0, 0, 0.15)',
                      color: '#5A5A5A'
                    }}
                  >
                    <p className="text-[12px]">Déposez un produit ici</p>
                  </div>
                ) : (
                  store.products.map(product => (
                    <DraggableProduct 
                      key={product.id} 
                      product={product} 
                      storeId={store.id}
                    />
                  ))
                )}
              </div>
            </div>
          )}
        </div>
      </PaperPoster>
    </div>
  );
};

function ListeScreenContent() {
  const [openAccordions, setOpenAccordions] = useState<number[]>([1]);
  const [stores, setStores] = useState<Store[]>([
    {
      id: 1,
      name: 'Lidl',
      address: '12 Rue de la République',
      position: { x: 30, y: 25 },
      color: '#2A9D8F',
      products: [
        { id: 1, name: 'Lait demi-écrémé 1L', price: 0.95, quantity: 2 },
        { id: 2, name: 'Pain de mie complet', price: 1.20, quantity: 1 },
        { id: 3, name: 'Yaourt nature x8', price: 2.10, quantity: 1 },
      ]
    },
    {
      id: 2,
      name: 'Carrefour',
      address: '45 Avenue du Centre',
      position: { x: 50, y: 50 },
      color: '#E9C46A',
      products: [
        { id: 4, name: 'Poulet fermier (1kg)', price: 8.50, quantity: 1 },
        { id: 5, name: 'Tomates cerises 250g', price: 2.30, quantity: 2 },
        { id: 6, name: 'Pâtes Barilla 500g', price: 1.85, quantity: 3 },
      ]
    },
    {
      id: 3,
      name: 'Leclerc',
      address: '8 Boulevard Victor Hugo',
      position: { x: 70, y: 75 },
      color: '#F4A261',
      products: [
        { id: 7, name: 'Café moulu 250g', price: 3.95, quantity: 1 },
        { id: 8, name: 'Jus d\'orange 1L', price: 2.50, quantity: 2 },
        { id: 9, name: 'Biscuits petit-déjeuner', price: 2.80, quantity: 1 },
      ]
    },
  ]);

  const toggleAccordion = (id: number) => {
    setOpenAccordions(prev => 
      prev.includes(id) 
        ? prev.filter(item => item !== id)
        : [...prev, id]
    );
  };

  const handleProductDrop = (productId: number, fromStoreId: number, toStoreId: number) => {
    setStores(prevStores => {
      const newStores = prevStores.map(store => ({
        ...store,
        products: [...store.products]
      }));

      // Find the product in the source store
      const fromStore = newStores.find(s => s.id === fromStoreId);
      const toStore = newStores.find(s => s.id === toStoreId);

      if (!fromStore || !toStore) return prevStores;

      const productIndex = fromStore.products.findIndex(p => p.id === productId);
      if (productIndex === -1) return prevStores;

      const [product] = fromStore.products.splice(productIndex, 1);
      toStore.products.push(product);

      return newStores;
    });
  };

  const getTotalForStore = (products: Product[]) => {
    return products.reduce((sum, product) => sum + (product.price * product.quantity), 0);
  };

  const grandTotal = stores.reduce((sum, store) => sum + getTotalForStore(store.products), 0);

  return (
    <div className="min-h-full pb-6" style={{ background: 'transparent' }}>
      {/* Map Card */}
      <div className="px-4 pt-4 mb-3">
        <PaperPoster rotation={-0.6} size="lg">
          <div 
            className="overflow-hidden relative"
            style={{ 
              height: '200px',
            }}
          >
            {/* Map Grid Background */}
            <svg className="absolute inset-0 w-full h-full opacity-20" style={{ zIndex: 1 }}>
              <defs>
                <pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse">
                  <path d="M 20 0 L 0 0 0 20" fill="none" stroke="rgba(0,0,0,0.2)" strokeWidth="0.5"/>
                </pattern>
              </defs>
              <rect width="100%" height="100%" fill="url(#grid)" />
            </svg>

            {/* Route Path */}
            <svg className="absolute inset-0 w-full h-full" style={{ zIndex: 2 }}>
              <defs>
                <filter id="glow-line">
                  <feGaussianBlur stdDeviation="1.5" result="coloredBlur"/>
                  <feMerge>
                    <feMergeNode in="coloredBlur"/>
                    <feMergeNode in="SourceGraphic"/>
                  </feMerge>
                </filter>
              </defs>
              {stores.map((store, index) => {
                if (index < stores.length - 1) {
                  const nextStore = stores[index + 1];
                  return (
                    <line
                      key={`line-${store.id}`}
                      x1={`${store.position.x}%`}
                      y1={`${store.position.y}%`}
                      x2={`${nextStore.position.x}%`}
                      y2={`${nextStore.position.y}%`}
                      stroke="#2A9D8F"
                      strokeWidth="2"
                      strokeDasharray="4 4"
                      filter="url(#glow-line)"
                      opacity="0.8"
                    />
                  );
                }
                return null;
              })}
            </svg>

            {/* Store Markers */}
            {stores.map((store, index) => (
              <div
                key={store.id}
                className="absolute"
                style={{
                  left: `${store.position.x}%`,
                  top: `${store.position.y}%`,
                  transform: 'translate(-50%, -50%)',
                  zIndex: 10
                }}
              >
                {/* Pin */}
                <div className="relative">
                  <div 
                    className="w-8 h-8 rounded-full flex items-center justify-center relative"
                    style={{ 
                      background: store.color,
                      boxShadow: `0 0 0 3px rgba(255, 255, 255, 0.3), 0 4px 12px ${store.color}80`
                    }}
                  >
                    <span className="text-white text-[12px] font-bold">{index + 1}</span>
                  </div>
                  {/* Label */}
                  <div 
                    className="absolute top-10 left-1/2 -translate-x-1/2 whitespace-nowrap px-2 py-1 rounded"
                    style={{ 
                      background: 'rgba(0, 0, 0, 0.8)',
                      fontSize: '10px',
                      color: '#ffffff'
                    }}
                  >
                    {store.name}
                  </div>
                </div>
              </div>
            ))}

            {/* Navigation Icon */}
            <div className="absolute top-3 right-3 z-20">
              <div 
                className="w-10 h-10 rounded-full flex items-center justify-center"
                style={{ 
                  background: 'rgba(42, 157, 143, 0.9)',
                  backdropFilter: 'blur(10px)',
                  boxShadow: '0 4px 12px rgba(0, 0, 0, 0.3)'
                }}
              >
                <Navigation className="w-5 h-5 text-white" />
              </div>
            </div>
          </div>
        </PaperPoster>
      </div>

      {/* Optimize Button */}
      <div className="px-4 mb-3">
        <button
          className="w-full py-3 rounded-xl font-bold text-[14px] relative overflow-hidden"
          style={{ 
            background: 'linear-gradient(135deg, #2A9D8F 0%, #1e7a6f 100%)',
            color: '#ffffff',
            boxShadow: '0 4px 16px rgba(42, 157, 143, 0.4)'
          }}
        >
          <div className="absolute inset-0 bg-white opacity-0 hover:opacity-10 transition-opacity" />
          <span className="relative z-10 flex items-center justify-center gap-2">
            <Navigation className="w-4 h-4" />
            Optimiser le trajet
          </span>
        </button>
      </div>

      {/* Shopping List Summary */}
      <div className="px-4 mb-3">
        <PaperPoster rotation={0.3} size="sm">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-[12px]" style={{ color: '#5A5A5A' }}>Total des courses</div>
              <div className="text-[#2A2A2A] text-[20px] font-bold">{grandTotal.toFixed(2)}€</div>
            </div>
            <div className="flex items-center gap-1">
              {stores.map(store => (
                <div 
                  key={store.id}
                  className="w-2 h-2 rounded-full"
                  style={{ background: store.color }}
                />
              ))}
            </div>
          </div>
        </PaperPoster>
      </div>

      {/* Store Accordions */}
      <div className="px-4 space-y-3">
        {stores.map((store, index) => {
          const isOpen = openAccordions.includes(store.id);

          return (
            <StoreAccordion
              key={store.id}
              store={store}
              index={index}
              isOpen={isOpen}
              onToggle={() => toggleAccordion(store.id)}
              onDrop={handleProductDrop}
            />
          );
        })}
      </div>
    </div>
  );
}

export function ListeScreen() {
  return (
    <DndProvider backend={HTML5Backend}>
      <ListeScreenContent />
    </DndProvider>
  );
}