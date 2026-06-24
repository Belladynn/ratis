export function BrickWallBackground() {
  return (
    <div className="fixed inset-0 z-0" style={{ background: '#1a2e38' }}>
      <svg 
        width="100%" 
        height="100%" 
        xmlns="http://www.w3.org/2000/svg"
        style={{ position: 'absolute', top: 0, left: 0 }}
      >
        <defs>
          <pattern id="brickPattern" x="0" y="0" width="200" height="100" patternUnits="userSpaceOnUse">
            {/* Première rangée de briques */}
            <rect x="0" y="0" width="98" height="48" fill="#1e3a3f" stroke="#152d32" strokeWidth="1"/>
            <rect x="100" y="0" width="98" height="48" fill="#1e3a3f" stroke="#152d32" strokeWidth="1"/>
            
            {/* Deuxième rangée décalée */}
            <rect x="-50" y="50" width="98" height="48" fill="#1a3439" stroke="#152d32" strokeWidth="1"/>
            <rect x="50" y="50" width="98" height="48" fill="#1a3439" stroke="#152d32" strokeWidth="1"/>
            <rect x="150" y="50" width="98" height="48" fill="#1a3439" stroke="#152d32" strokeWidth="1"/>
            
            {/* Variations de couleur pour réalisme */}
            <rect x="10" y="5" width="30" height="15" fill="#1f3b40" opacity="0.3"/>
            <rect x="110" y="8" width="25" height="12" fill="#1f3b40" opacity="0.2"/>
            <rect x="60" y="55" width="35" height="18" fill="#1c3638" opacity="0.3"/>
            <rect x="160" y="60" width="28" height="14" fill="#1c3638" opacity="0.2"/>
          </pattern>
          
          {/* Gradient d'ombre pour profondeur */}
          <linearGradient id="shadowGradient" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stopColor="#000000" stopOpacity="0.3"/>
            <stop offset="100%" stopColor="#000000" stopOpacity="0.1"/>
          </linearGradient>
        </defs>
        
        <rect width="100%" height="100%" fill="url(#brickPattern)"/>
        <rect width="100%" height="100%" fill="url(#shadowGradient)"/>
      </svg>
      
      {/* Texture overlay subtile */}
      <div 
        className="absolute inset-0 opacity-[0.03]"
        style={{
          backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 400 400' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' /%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)' /%3E%3C/svg%3E")`,
        }}
      />
    </div>
  );
}
