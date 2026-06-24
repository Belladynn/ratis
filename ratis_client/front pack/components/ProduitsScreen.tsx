import { Camera, HelpCircle, Tag, Calendar, Package, Barcode, Scale, ShoppingCart } from 'lucide-react';
import { ImageWithFallback } from './figma/ImageWithFallback';
import { PaperPoster } from './PaperPoster';

interface ProductDetail {
  id: string;
  label: string;
  value: string;
  icon: any;
  size: 'small' | 'medium' | 'large';
  color: string;
}

export function ProduitsScreen() {
  const productDetails: ProductDetail[] = [
    {
      id: 'price',
      label: 'Prix',
      value: '2.10€',
      icon: ShoppingCart,
      size: 'large',
      color: '#2A9D8F'
    },
    {
      id: 'category',
      label: 'Catégorie',
      value: 'Produits laitiers',
      icon: Tag,
      size: 'medium',
      color: '#E9C46A'
    },
    {
      id: 'brand',
      label: 'Marque',
      value: 'Bio Nature',
      icon: Package,
      size: 'small',
      color: '#F4A261'
    },
    {
      id: 'added',
      label: 'Ajouté le',
      value: '11 Avril 2026',
      icon: Calendar,
      size: 'medium',
      color: '#2A9D8F'
    },
    {
      id: 'barcode',
      label: 'Code-barres',
      value: '3245678901234',
      icon: Barcode,
      size: 'medium',
      color: '#E76F51'
    },
    {
      id: 'weight',
      label: 'Poids',
      value: '125g x 8',
      icon: Scale,
      size: 'small',
      color: '#2A9D8F'
    },
  ];

  return (
    <div className="min-h-full pb-6" style={{ background: 'transparent' }}>
      {/* Product Image */}
      <div className="px-4 pt-4 mb-6">
        <PaperPoster rotation={-0.8} size="lg">
          <div 
            className="overflow-hidden relative"
            style={{ 
              height: '240px',
            }}
          >
            <ImageWithFallback
              src="https://images.unsplash.com/photo-1604095853918-1a1823a63dd5?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w3Nzg4Nzd8MHwxfHNlYXJjaHwxfHxvcmdhbmljJTIweW9ndXJ0JTIwcHJvZHVjdCUyMHBhY2thZ2luZ3xlbnwxfHx8fDE3NzU5MTk0NDZ8MA&ixlib=rb-4.1.0&q=80&w=1080&utm_source=figma&utm_medium=referral"
              alt="Yaourt nature x8"
              className="w-full h-full object-cover"
            />

            {/* Product Name Overlay */}
            <div 
              className="absolute bottom-0 left-0 right-0 p-4 z-20"
              style={{
                background: 'linear-gradient(to top, rgba(0, 0, 0, 0.8), transparent)'
              }}
            >
              <h1 className="text-white text-[20px] font-bold">
                Yaourt nature Bio x8
              </h1>
              <p className="text-[12px] mt-1" style={{ color: '#B0BEC5' }}>
                Produit laitier biologique
              </p>
            </div>
          </div>
        </PaperPoster>
      </div>

      {/* Camera Icon */}
      <div className="px-4 mb-3 flex justify-center">
        <button
          className="w-12 h-12 rounded-full flex items-center justify-center relative"
          style={{ 
            background: 'linear-gradient(135deg, #2A9D8F 0%, #1e7a6f 100%)',
            boxShadow: '0 8px 24px rgba(42, 157, 143, 0.5)'
          }}
        >
          <div className="absolute inset-0 bg-white opacity-0 hover:opacity-10 transition-opacity rounded-full" />
          <Camera className="w-6 h-6 text-white relative z-10" />
        </button>
      </div>

      {/* Product Details - Organic Layout */}
      <div className="px-4">
        <div className="text-white text-[14px] font-bold mb-3 px-1" style={{ textShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>
          Caractéristiques
        </div>

        {/* Single Box with all characteristics */}
        <PaperPoster rotation={0.6} size="lg">
          <div className="relative z-10 space-y-4">
            {/* Price - Large */}
            <div className="flex items-center justify-between pb-4 border-b" style={{ borderColor: 'rgba(0, 0, 0, 0.08)' }}>
              <div className="flex items-center gap-3">
                <div 
                  className="w-10 h-10 rounded-full flex items-center justify-center"
                  style={{ 
                    background: `${productDetails[0].color}30`,
                    border: `2px solid ${productDetails[0].color}`
                  }}
                >
                  <ShoppingCart className="w-5 h-5" style={{ 
                    color: productDetails[0].color,
                    filter: 'drop-shadow(0 0 1px white) drop-shadow(0 0 1px white)'
                  }} />
                </div>
                <div>
                  <div className="text-[11px]" style={{ color: '#5A5A5A' }}>
                    {productDetails[0].label}
                  </div>
                  <div className="text-[#2A2A2A] text-[24px] font-bold leading-none mt-1">
                    {productDetails[0].value}
                  </div>
                </div>
              </div>
              <button
                className="w-7 h-7 rounded-full flex items-center justify-center"
                style={{ 
                  background: 'rgba(0, 0, 0, 0.1)',
                  border: '1px solid rgba(0, 0, 0, 0.15)'
                }}
              >
                <HelpCircle className="w-4 h-4" style={{ color: '#5A5A5A' }} />
              </button>
            </div>

            {/* Category */}
            <div className="flex items-center justify-between py-3 border-b" style={{ borderColor: 'rgba(0, 0, 0, 0.08)' }}>
              <div className="flex items-center gap-3">
                <div 
                  className="w-9 h-9 rounded-full flex items-center justify-center"
                  style={{ 
                    background: `${productDetails[1].color}30`,
                    border: `1px solid ${productDetails[1].color}60`
                  }}
                >
                  <Tag className="w-4 h-4" style={{ color: productDetails[1].color }} />
                </div>
                <div>
                  <div className="text-[11px]" style={{ color: '#5A5A5A' }}>
                    {productDetails[1].label}
                  </div>
                  <div className="text-[#2A2A2A] text-[14px] font-bold">
                    {productDetails[1].value}
                  </div>
                </div>
              </div>
              <button
                className="w-7 h-7 rounded-full flex items-center justify-center"
                style={{ 
                  background: 'rgba(0, 0, 0, 0.1)',
                  border: '1px solid rgba(0, 0, 0, 0.15)'
                }}
              >
                <HelpCircle className="w-4 h-4" style={{ color: '#5A5A5A' }} />
              </button>
            </div>

            {/* Brand */}
            <div className="flex items-center justify-between py-3 border-b" style={{ borderColor: 'rgba(0, 0, 0, 0.08)' }}>
              <div className="flex items-center gap-3">
                <div 
                  className="w-9 h-9 rounded-full flex items-center justify-center"
                  style={{ 
                    background: `${productDetails[2].color}30`,
                    border: `1px solid ${productDetails[2].color}60`
                  }}
                >
                  <Package className="w-4 h-4" style={{ color: productDetails[2].color }} />
                </div>
                <div>
                  <div className="text-[11px]" style={{ color: '#5A5A5A' }}>
                    {productDetails[2].label}
                  </div>
                  <div className="text-[#2A2A2A] text-[14px] font-bold">
                    {productDetails[2].value}
                  </div>
                </div>
              </div>
              <button
                className="w-7 h-7 rounded-full flex items-center justify-center"
                style={{ 
                  background: 'rgba(0, 0, 0, 0.1)',
                  border: '1px solid rgba(0, 0, 0, 0.15)'
                }}
              >
                <HelpCircle className="w-4 h-4" style={{ color: '#5A5A5A' }} />
              </button>
            </div>

            {/* Added Date */}
            <div className="flex items-center justify-between py-3 border-b" style={{ borderColor: 'rgba(0, 0, 0, 0.08)' }}>
              <div className="flex items-center gap-3">
                <div 
                  className="w-9 h-9 rounded-full flex items-center justify-center"
                  style={{ 
                    background: `${productDetails[3].color}30`,
                    border: `1px solid ${productDetails[3].color}60`
                  }}
                >
                  <Calendar className="w-4 h-4" style={{ color: productDetails[3].color }} />
                </div>
                <div>
                  <div className="text-[11px]" style={{ color: '#5A5A5A' }}>
                    {productDetails[3].label}
                  </div>
                  <div className="text-[#2A2A2A] text-[14px] font-bold">
                    {productDetails[3].value}
                  </div>
                </div>
              </div>
              <button
                className="w-7 h-7 rounded-full flex items-center justify-center"
                style={{ 
                  background: 'rgba(0, 0, 0, 0.1)',
                  border: '1px solid rgba(0, 0, 0, 0.15)'
                }}
              >
                <HelpCircle className="w-4 h-4" style={{ color: '#5A5A5A' }} />
              </button>
            </div>

            {/* Barcode */}
            <div className="flex items-center justify-between py-3 border-b" style={{ borderColor: 'rgba(0, 0, 0, 0.08)' }}>
              <div className="flex items-center gap-3">
                <div 
                  className="w-9 h-9 rounded-full flex items-center justify-center"
                  style={{ 
                    background: `${productDetails[4].color}30`,
                    border: `1px solid ${productDetails[4].color}60`
                  }}
                >
                  <Barcode className="w-4 h-4" style={{ color: productDetails[4].color }} />
                </div>
                <div>
                  <div className="text-[11px]" style={{ color: '#5A5A5A' }}>
                    {productDetails[4].label}
                  </div>
                  <div className="text-[#2A2A2A] text-[12px] font-bold font-mono">
                    {productDetails[4].value}
                  </div>
                </div>
              </div>
              <button
                className="w-7 h-7 rounded-full flex items-center justify-center"
                style={{ 
                  background: 'rgba(0, 0, 0, 0.1)',
                  border: '1px solid rgba(0, 0, 0, 0.15)'
                }}
              >
                <HelpCircle className="w-4 h-4" style={{ color: '#5A5A5A' }} />
              </button>
            </div>

            {/* Weight */}
            <div className="flex items-center justify-between pt-3">
              <div className="flex items-center gap-3">
                <div 
                  className="w-9 h-9 rounded-full flex items-center justify-center"
                  style={{ 
                    background: `${productDetails[5].color}30`,
                    border: `1px solid ${productDetails[5].color}60`
                  }}
                >
                  <Scale className="w-4 h-4" style={{ color: productDetails[5].color }} />
                </div>
                <div>
                  <div className="text-[11px]" style={{ color: '#5A5A5A' }}>
                    {productDetails[5].label}
                  </div>
                  <div className="text-[#2A2A2A] text-[14px] font-bold">
                    {productDetails[5].value}
                  </div>
                </div>
              </div>
              <button
                className="w-7 h-7 rounded-full flex items-center justify-center"
                style={{ 
                  background: 'rgba(0, 0, 0, 0.1)',
                  border: '1px solid rgba(0, 0, 0, 0.15)'
                }}
              >
                <HelpCircle className="w-4 h-4" style={{ color: '#5A5A5A' }} />
              </button>
            </div>
          </div>
        </PaperPoster>
      </div>
    </div>
  );
}