import { Outlet, useLocation, Link } from "react-router";
import { PieChart, List, Camera, Milk, Settings, Gift } from "lucide-react";
import { BrickWallBackground } from "./BrickWallBackground";

export function RootLayout() {
  const location = useLocation();

  const tabs = [
    { path: "/RatisApp/dashboard", icon: PieChart, label: "Dashboard" },
    { path: "/RatisApp/liste", icon: List, label: "Liste" },
    { path: "/RatisApp/scan", icon: Camera, label: "Scan" },
    { path: "/RatisApp/produits", icon: Milk, label: "Produits" },
    { path: "/RatisApp/profil", icon: Settings, label: "Profil" },
  ];

  return (
    <div className="h-full flex flex-col" style={{ 
      height: '100dvh', /* Dynamic viewport height for mobile */
      maxHeight: '-webkit-fill-available' /* Safari fix */
    }}>
      {/* Brick Wall Background */}
      <BrickWallBackground />
      
      {/* Status Bar - Use safe-area-inset for notch support */}
      <div 
        className="flex items-center justify-between px-8 text-white text-sm font-medium relative z-10 shrink-0" 
        style={{ 
          background: '#1a2e38',
          height: '44px',
          paddingTop: 'max(0.5rem, env(safe-area-inset-top))'
        }}
      >
        <span>9:41</span>
        <div className="w-20 h-6 bg-black rounded-full"></div>
        <div className="flex items-center gap-1">
          <svg className="w-4 h-3" viewBox="0 0 16 12" fill="none">
            <rect x="0" y="3" width="4" height="6" rx="1" fill="white" opacity="0.4"/>
            <rect x="6" y="2" width="4" height="8" rx="1" fill="white" opacity="0.6"/>
            <rect x="12" y="0" width="4" height="12" rx="1" fill="white"/>
          </svg>
          <svg className="w-3 h-3 ml-1" viewBox="0 0 12 12" fill="white">
            <path d="M2 2h8v6H2z"/>
            <path d="M11 5h1v2h-1z" opacity="0.4"/>
          </svg>
        </div>
      </div>

      {/* [GLOBAL_HEADER] */}
      <div 
        className="flex items-center px-4 gap-2 shrink-0 w-full relative z-10"
        style={{ 
          background: '#1a2e38',
          height: '36px',
        }}
      >
        {/* [HEADER_BATTLEPASS] */}
        <div className="flex-1">
          <div className="text-[11px] mb-0.5" style={{ color: '#78909C' }}>
            Saison 1
          </div>
          <div 
            className="h-1.5 rounded-full relative"
            style={{ background: '#1a2e38' }}
          >
            <div 
              className="h-full rounded-full"
              style={{ 
                background: '#E9C46A',
                width: '58%'
              }}
            />
            {/* Tick marks */}
            {[25, 50, 75, 100].map((pos) => (
              <div
                key={pos}
                className="absolute top-0 w-0.5 h-1.5"
                style={{
                  left: `${pos}%`,
                  background: '#333533'
                }}
              />
            ))}
          </div>
        </div>

        {/* [HEADER_CABECOINS] */}
        <div 
          className="px-2.5 py-1 rounded-full flex items-center gap-1.5"
          style={{ background: '#1a2e38' }}
        >
          <div 
            className="w-3 h-3 rounded-full"
            style={{ background: '#E9C46A' }}
          />
          <span className="text-[12px] font-bold text-white">1 240</span>
        </div>

        {/* [HEADER_GIFT] */}
        <div className="relative">
          <Gift className="w-5 h-5" style={{ color: '#2A9D8F' }} />
        </div>

        {/* [HEADER_MISSIONS] */}
        <div className="relative">
          <Calendar className="w-5 h-5" style={{ color: '#2A9D8F' }} />
          <div 
            className="absolute -top-0.5 -right-0.5 w-1.5 h-1.5 rounded-full"
            style={{ background: '#F4A261' }}
          />
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto relative z-10">
        <Outlet />
      </div>

      {/* [BOTTOM_TAB_BAR] */}
      <div 
        className="flex items-center justify-around shrink-0 border-t relative z-10"
        style={{ 
          background: '#1a2e38',
          height: '60px',
          paddingBottom: 'max(0.5rem, env(safe-area-inset-bottom))', /* Support for home indicator on iPhone */
          borderTopColor: '#152d32',
          borderTopWidth: '1px'
        }}
      >
        {tabs.map((tab) => {
          const isActive = location.pathname === tab.path;
          const Icon = tab.icon;
          const isCameraIcon = tab.path === "/RatisApp/scan";
          
          return (
            <Link
              key={tab.path}
              to={tab.path}
              className="flex items-center justify-center w-14 h-14"
            >
              <Icon 
                className={isCameraIcon ? "w-8 h-8" : "w-6 h-6"}
                style={{ 
                  color: isActive ? '#2A9D8F' : '#78909C',
                }} 
                strokeWidth={isActive ? 2.5 : 2}
              />
            </Link>
          );
        })}
      </div>
    </div>
  );
}

function Calendar({ className, style }: { className: string; style: React.CSSProperties }) {
  return (
    <svg
      className={className}
      style={style}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
      <line x1="16" y1="2" x2="16" y2="6" />
      <line x1="8" y1="2" x2="8" y2="6" />
      <line x1="3" y1="10" x2="21" y2="10" />
    </svg>
  );
}