import { useState, useRef } from 'react';
import { Camera, ChevronDown, Barcode, HelpCircle, Coins, X } from 'lucide-react';
import { PaperPoster } from './PaperPoster';

interface ScannedProduct {
  id: number;
  name: string;
  price: number;
}

interface ScannedReceipt {
  id: number;
  storeName: string;
  date: string;
  points: number;
  products: ScannedProduct[];
}

interface ScannedLabel {
  id: number;
  name: string;
  price: number;
  date: string;
}

export function ScanScreen() {
  const [isCameraOpen, setIsCameraOpen] = useState(false);
  const [openAccordions, setOpenAccordions] = useState<number[]>([1]);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);

  const scannedReceipts: ScannedReceipt[] = [
    {
      id: 1,
      storeName: 'Lidl',
      date: '11 Avril 2026',
      points: 150,
      products: [
        { id: 1, name: 'Lait demi-écrémé 1L', price: 0.95 },
        { id: 2, name: 'Pain de mie complet', price: 1.20 },
        { id: 3, name: 'Yaourt nature x8', price: 2.10 },
      ]
    },
    {
      id: 2,
      storeName: 'Carrefour',
      date: '10 Avril 2026',
      points: 280,
      products: [
        { id: 4, name: 'Poulet fermier (1kg)', price: 8.50 },
        { id: 5, name: 'Tomates cerises 250g', price: 2.30 },
        { id: 6, name: 'Pâtes Barilla 500g', price: 1.85 },
        { id: 7, name: 'Fromage râpé 200g', price: 2.95 },
      ]
    },
    {
      id: 3,
      storeName: 'Leclerc',
      date: '08 Avril 2026',
      points: 95,
      products: [
        { id: 8, name: 'Café moulu 250g', price: 3.95 },
        { id: 9, name: 'Jus d\'orange 1L', price: 2.50 },
      ]
    },
  ];

  const scannedLabels: ScannedLabel[] = [
    { id: 1, name: 'Biscuits petit-déjeuner', price: 2.80, date: '09 Avril 2026' },
    { id: 2, name: 'Chips saveur barbecue', price: 1.45, date: '07 Avril 2026' },
    { id: 3, name: 'Eau minérale 6x1.5L', price: 3.20, date: '06 Avril 2026' },
  ];

  const toggleAccordion = (id: number) => {
    setOpenAccordions(prev => 
      prev.includes(id) 
        ? prev.filter(item => item !== id)
        : [...prev, id]
    );
  };

  const openCamera = async () => {
    try {
      const mediaStream = await navigator.mediaDevices.getUserMedia({ 
        video: { facingMode: 'environment' } 
      });
      setStream(mediaStream);
      setIsCameraOpen(true);
      
      if (videoRef.current) {
        videoRef.current.srcObject = mediaStream;
      }
    } catch (error) {
      console.error('Erreur d\'accès à la caméra:', error);
      alert('Impossible d\'accéder à la caméra. Veuillez vérifier les permissions.');
    }
  };

  const closeCamera = () => {
    if (stream) {
      stream.getTracks().forEach(track => track.stop());
      setStream(null);
    }
    setIsCameraOpen(false);
  };

  const getTotalForReceipt = (products: ScannedProduct[]) => {
    return products.reduce((sum, product) => sum + product.price, 0);
  };

  return (
    <div className="min-h-full pb-6" style={{ background: 'transparent' }}>
      {/* Camera Module */}
      <div className="px-4 pt-4 mb-4">
        <PaperPoster rotation={-0.7} size="lg">
          <div 
            className="overflow-hidden relative"
            style={{ 
              height: isCameraOpen ? '280px' : '140px',
              background: 'rgba(42, 157, 143, 0.1)',
              transition: 'height 0.3s ease'
            }}
          >
            {!isCameraOpen ? (
              // Camera Trigger
              <button
                onClick={openCamera}
                className="w-full h-full flex flex-col items-center justify-center gap-3 relative z-10"
              >
                <div 
                  className="w-16 h-16 rounded-full flex items-center justify-center"
                  style={{ 
                    background: 'linear-gradient(135deg, #2A9D8F 0%, #1e7a6f 100%)',
                    boxShadow: '0 8px 24px rgba(42, 157, 143, 0.5)'
                  }}
                >
                  <Camera className="w-8 h-8 text-white" />
                </div>
                <div className="text-center">
                  <div className="text-[#2A2A2A] text-[16px] font-bold mb-1">
                    Scanner un ticket ou produit
                  </div>
                  <div className="text-[12px]" style={{ color: '#5A5A5A' }}>
                    Appuyez pour ouvrir la caméra
                  </div>
                </div>
              </button>
            ) : (
              // Camera View
              <div className="relative w-full h-full">
                <video
                  ref={videoRef}
                  autoPlay
                  playsInline
                  className="w-full h-full object-cover"
                />
                
                {/* Camera Overlay */}
                <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                  {/* Scanning Frame */}
                  <div 
                    className="relative"
                    style={{ 
                      width: '80%',
                      height: '60%',
                      border: '2px solid #2A9D8F',
                      borderRadius: '12px',
                      boxShadow: '0 0 0 9999px rgba(0, 0, 0, 0.5)'
                    }}
                  >
                    {/* Corner indicators */}
                    <div className="absolute top-0 left-0 w-6 h-6 border-t-4 border-l-4 border-[#2A9D8F]" style={{ borderRadius: '12px 0 0 0' }} />
                    <div className="absolute top-0 right-0 w-6 h-6 border-t-4 border-r-4 border-[#2A9D8F]" style={{ borderRadius: '0 12px 0 0' }} />
                    <div className="absolute bottom-0 left-0 w-6 h-6 border-b-4 border-l-4 border-[#2A9D8F]" style={{ borderRadius: '0 0 0 12px' }} />
                    <div className="absolute bottom-0 right-0 w-6 h-6 border-b-4 border-r-4 border-[#2A9D8F]" style={{ borderRadius: '0 0 12px 0' }} />
                  </div>
                </div>

                {/* Close Button */}
                <button
                  onClick={closeCamera}
                  className="absolute top-3 right-3 z-20 w-10 h-10 rounded-full flex items-center justify-center pointer-events-auto"
                  style={{ 
                    background: 'rgba(0, 0, 0, 0.7)',
                    backdropFilter: 'blur(10px)'
                  }}
                >
                  <X className="w-5 h-5 text-white" />
                </button>

                {/* Instructions */}
                <div 
                  className="absolute bottom-3 left-1/2 -translate-x-1/2 px-4 py-2 rounded-full text-[11px] text-white font-bold z-20 whitespace-nowrap"
                  style={{ 
                    background: 'rgba(0, 0, 0, 0.7)',
                    backdropFilter: 'blur(10px)'
                  }}
                >
                  Centrez le ticket ou le code-barres
                </div>
              </div>
            )}
          </div>
        </PaperPoster>
      </div>

      {/* Scan Receipts Section */}
      <div className="px-4 mb-4">
        <div className="flex items-center gap-2 mb-3">
          <div className="text-white text-[14px] font-bold" style={{ textShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>Tickets scannés</div>
          <div 
            className="px-2 py-0.5 rounded-full text-[11px] font-bold"
            style={{ 
              background: 'rgba(212, 175, 135, 0.3)',
              border: '1px solid rgba(212, 175, 135, 0.5)',
              color: '#2A9D8F'
            }}
          >
            {scannedReceipts.length}
          </div>
        </div>

        <div className="space-y-3">
          {scannedReceipts.map((receipt, index) => {
            const isOpen = openAccordions.includes(receipt.id);
            const receiptTotal = getTotalForReceipt(receipt.products);

            return (
              <PaperPoster 
                key={receipt.id}
                rotation={index % 3 === 0 ? 0.5 : index % 3 === 1 ? -0.5 : 0.2}
                size="md"
              >
                {/* Accordion Header */}
                <button
                  onClick={() => toggleAccordion(receipt.id)}
                  className="w-full flex items-center justify-between relative z-10"
                >
                  <div className="flex-1 text-left">
                    <div className="text-[#2A2A2A] text-[15px] font-bold">
                      {receipt.storeName} - {receipt.date}
                    </div>
                    <div className="text-[11px] mt-0.5" style={{ color: '#5A5A5A' }}>
                      {receipt.products.length} articles • {receiptTotal.toFixed(2)}€
                    </div>
                  </div>

                  <div className="flex items-center gap-3">
                    {/* Points Badge */}
                    <div 
                      className="flex items-center gap-1 px-2.5 py-1 rounded-full"
                      style={{ 
                        background: 'rgba(233, 196, 106, 0.3)',
                        border: '1px solid rgba(233, 196, 106, 0.5)'
                      }}
                    >
                      <Coins className="w-3.5 h-3.5" style={{ 
                        color: '#E9C46A',
                        filter: 'drop-shadow(0 0 2px rgba(0,0,0,0.6)) drop-shadow(0 0 3px rgba(0,0,0,0.5))'
                      }} />
                      <span className="text-[12px] font-bold" style={{ 
                        color: '#E9C46A',
                        textShadow: '0 0 3px rgba(0,0,0,0.6), 0 0 5px rgba(0,0,0,0.4)'
                      }}>
                        {receipt.points}
                      </span>
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
                      {receipt.products.map(product => (
                        <div
                          key={product.id}
                          className="flex items-center justify-between py-2 px-3 rounded-lg"
                          style={{ 
                            background: 'rgba(42, 157, 143, 0.1)',
                          }}
                        >
                          <div className="text-[13px]" style={{ color: '#2A2A2A' }}>
                            {product.name}
                          </div>
                          <div className="text-[#2A2A2A] text-[14px] font-bold">
                            {product.price.toFixed(2)}€
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </PaperPoster>
            );
          })}
        </div>
      </div>

      {/* Scanned Labels Section */}
      <div className="px-4">
        <div className="flex items-center gap-2 mb-3">
          <div className="text-white text-[14px] font-bold" style={{ textShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>Étiquettes scannées</div>
          <div 
            className="px-2 py-0.5 rounded-full text-[11px] font-bold"
            style={{ 
              background: 'rgba(212, 175, 135, 0.3)',
              border: '1px solid rgba(212, 175, 135, 0.5)',
              color: '#E9C46A'
            }}
          >
            {scannedLabels.length}
          </div>
        </div>

        <div className="space-y-3">
          {scannedLabels.map((label, index) => (
            <PaperPoster 
              key={label.id}
              rotation={index % 2 === 0 ? -0.4 : 0.4}
              size="sm"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div 
                    className="w-10 h-10 rounded-lg flex items-center justify-center"
                    style={{ 
                      background: 'rgba(42, 157, 143, 0.2)',
                      border: '1px solid rgba(42, 157, 143, 0.4)'
                    }}
                  >
                    <Barcode className="w-5 h-5" style={{ color: '#2A9D8F' }} />
                  </div>
                  <div>
                    <div className="text-[#2A2A2A] text-[14px] font-bold">{label.name}</div>
                    <div className="text-[11px]" style={{ color: '#5A5A5A' }}>{label.date}</div>
                  </div>
                </div>
                <div className="text-[#2A2A2A] text-[16px] font-bold">{label.price.toFixed(2)}€</div>
              </div>
            </PaperPoster>
          ))}
        </div>
      </div>
    </div>
  );
}