import React, { ReactNode } from 'react';

interface PaperPosterProps {
  children: ReactNode;
  className?: string;
  rotation?: number; // Légère rotation en degrés
  pinColors?: string[]; // Couleurs des pins
  size?: 'sm' | 'md' | 'lg';
}

export function PaperPoster({ 
  children, 
  className = '', 
  rotation = 0,
  pinColors = ['#E63946', '#FFB800', '#2A9D8F', '#A855F7'],
  size = 'md'
}: PaperPosterProps) {
  // Sélection aléatoire mais déterministe d'une couleur de pin
  const pinColor = pinColors[Math.floor(Math.random() * pinColors.length)];
  
  const sizeClasses = {
    sm: 'p-3',
    md: 'p-4',
    lg: 'p-5'
  };

  return (
    <div 
      className={`relative ${className}`}
      style={{
        transform: `rotate(${rotation}deg)`,
        transition: 'transform 0.2s ease',
      }}
    >
      {/* Pin gauche */}
      <div 
        className="absolute -top-2 left-6 z-20"
        style={{
          width: '12px',
          height: '12px',
        }}
      >
        {/* Ombre du pin */}
        <div 
          className="absolute inset-0 rounded-full blur-sm"
          style={{
            background: 'rgba(0,0,0,0.3)',
            transform: 'translateY(2px)'
          }}
        />
        {/* Pin */}
        <div 
          className="relative w-full h-full rounded-full"
          style={{
            background: `radial-gradient(circle at 30% 30%, ${pinColor}, ${pinColor}DD)`,
            boxShadow: `inset -1px -1px 2px rgba(0,0,0,0.3), inset 1px 1px 1px rgba(255,255,255,0.3)`,
          }}
        >
          {/* Reflet du pin */}
          <div 
            className="absolute top-0.5 left-0.5 w-1.5 h-1.5 rounded-full"
            style={{
              background: 'rgba(255,255,255,0.6)',
            }}
          />
        </div>
        {/* Pointe du pin */}
        <div 
          className="absolute top-full left-1/2 w-0.5 h-1"
          style={{
            background: '#666',
            transform: 'translateX(-50%)',
            boxShadow: '0 1px 2px rgba(0,0,0,0.3)'
          }}
        />
      </div>

      {/* Pin droit */}
      <div 
        className="absolute -top-2 right-6 z-20"
        style={{
          width: '12px',
          height: '12px',
        }}
      >
        {/* Ombre du pin */}
        <div 
          className="absolute inset-0 rounded-full blur-sm"
          style={{
            background: 'rgba(0,0,0,0.3)',
            transform: 'translateY(2px)'
          }}
        />
        {/* Pin */}
        <div 
          className="relative w-full h-full rounded-full"
          style={{
            background: `radial-gradient(circle at 30% 30%, ${pinColors[(pinColors.indexOf(pinColor) + 1) % pinColors.length]}, ${pinColors[(pinColors.indexOf(pinColor) + 1) % pinColors.length]}DD)`,
            boxShadow: `inset -1px -1px 2px rgba(0,0,0,0.3), inset 1px 1px 1px rgba(255,255,255,0.3)`,
          }}
        >
          {/* Reflet du pin */}
          <div 
            className="absolute top-0.5 left-0.5 w-1.5 h-1.5 rounded-full"
            style={{
              background: 'rgba(255,255,255,0.6)',
            }}
          />
        </div>
        {/* Pointe du pin */}
        <div 
          className="absolute top-full left-1/2 w-0.5 h-1"
          style={{
            background: '#666',
            transform: 'translateX(-50%)',
            boxShadow: '0 1px 2px rgba(0,0,0,0.3)'
          }}
        />
      </div>

      {/* L'affiche en papier craft */}
      <div 
        className={`relative ${sizeClasses[size]}`}
        style={{
          background: '#D4A574',
          boxShadow: `
            0 4px 6px rgba(0,0,0,0.3),
            0 8px 15px rgba(0,0,0,0.2)
          `,
        }}
      >
        {/* Contenu */}
        <div className="relative z-10">
          {children}
        </div>
      </div>
    </div>
  );
}

// Variante pour les cartes interactives
export function InteractivePaperPoster(props: PaperPosterProps & { onClick?: () => void }) {
  const { onClick, ...posterProps } = props;
  
  return (
    <div 
      onClick={onClick}
      className="cursor-pointer active:scale-[0.98] transition-transform"
      style={{
        transformOrigin: 'center center'
      }}
    >
      <PaperPoster {...posterProps} />
    </div>
  );
}