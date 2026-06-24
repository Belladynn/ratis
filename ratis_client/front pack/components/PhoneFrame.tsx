import React from 'react';

interface PhoneFrameProps {
  children: React.ReactNode;
}

export function PhoneFrame({ children }: PhoneFrameProps) {
  return (
    <div 
      className="min-h-screen flex items-center justify-center p-4 sm:p-8" 
      style={{ background: 'linear-gradient(to bottom right, #1a1a2e, #16213e)' }}
    >
      {/* Mobile Phone Frame */}
      <div className="relative">
        {/* Phone Frame */}
        <div className="w-[375px] h-[812px] bg-black rounded-[3rem] p-3 shadow-2xl">
          {/* Screen */}
          <div className="w-full h-full rounded-[2.5rem] overflow-hidden relative">
            {children}
          </div>
        </div>

        {/* Phone Details */}
        <div className="absolute -bottom-12 left-1/2 transform -translate-x-1/2 text-center">
          <p className="text-sm text-gray-400">iPhone 14 Pro - 375×812</p>
        </div>
      </div>
    </div>
  );
}
