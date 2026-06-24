import { Mail, Lock, ArrowRight, Eye, EyeOff } from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router';
import { PaperPoster } from './PaperPoster';
import { BrickWallBackground } from './BrickWallBackground';

export function LoginScreen() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const navigate = useNavigate();

  const handleLogin = (e: React.FormEvent) => {
    e.preventDefault();
    // Simulation de connexion
    navigate('/RatisApp/dashboard');
  };

  return (
    <div className="h-full relative overflow-hidden flex items-center justify-center" style={{
      height: '100dvh',
      maxHeight: '-webkit-fill-available'
    }}>
      {/* Brick Wall Background */}
      <BrickWallBackground />
      
      {/* Content */}
      <div className="relative z-10 w-full max-w-md px-4">
        {/* Logo / Mascot Section */}
        <div className="text-center mb-8">
          <div className="inline-block mb-4">
            <img 
              src="/imports/image.png" 
              alt="Ratis"
              className="w-24 h-24 object-contain mx-auto"
              style={{
                filter: 'drop-shadow(0 4px 20px rgba(255, 184, 0, 0.4))'
              }}
            />
          </div>
          
          <h1 className="text-white text-[32px] font-black mb-2" style={{ textShadow: '0 2px 8px rgba(0,0,0,0.5)' }}>
            Ratis
          </h1>
          <p className="text-[14px]" style={{ color: '#CBD5E1', textShadow: '0 1px 4px rgba(0,0,0,0.5)' }}>
            Le rat malin qui trouve les meilleurs prix 🛒
          </p>
        </div>

        {/* Login Form */}
        <form onSubmit={handleLogin}>
          <PaperPoster rotation={-0.5} size="lg">
            <h2 className="text-[#2A2A2A] text-[20px] font-bold mb-6">
              Connexion
            </h2>

            {/* Email Input */}
            <div className="mb-4">
              <label className="block text-[12px] font-bold mb-2" style={{ color: '#5A5A5A' }}>
                Email
              </label>
              <div className="relative">
                <div className="absolute left-3 top-1/2 -translate-y-1/2">
                  <Mail className="w-5 h-5" style={{ color: '#64748B' }} />
                </div>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="ton-email@exemple.com"
                  className="w-full pl-11 pr-4 py-3 rounded-xl text-[#2A2A2A] text-[14px] outline-none transition-all"
                  style={{
                    background: 'rgba(255, 255, 255, 0.6)',
                    border: '2px solid transparent',
                  }}
                  onFocus={(e) => e.target.style.borderColor = '#2A9D8F'}
                  onBlur={(e) => e.target.style.borderColor = 'transparent'}
                  required
                />
              </div>
            </div>

            {/* Password Input */}
            <div className="mb-6">
              <label className="block text-[12px] font-bold mb-2" style={{ color: '#5A5A5A' }}>
                Mot de passe
              </label>
              <div className="relative">
                <div className="absolute left-3 top-1/2 -translate-y-1/2">
                  <Lock className="w-5 h-5" style={{ color: '#64748B' }} />
                </div>
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  className="w-full pl-11 pr-12 py-3 rounded-xl text-[#2A2A2A] text-[14px] outline-none transition-all"
                  style={{
                    background: 'rgba(255, 255, 255, 0.6)',
                    border: '2px solid transparent',
                  }}
                  onFocus={(e) => e.target.style.borderColor = '#2A9D8F'}
                  onBlur={(e) => e.target.style.borderColor = 'transparent'}
                  required
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2"
                >
                  {showPassword ? (
                    <EyeOff className="w-5 h-5" style={{ color: '#64748B' }} />
                  ) : (
                    <Eye className="w-5 h-5" style={{ color: '#64748B' }} />
                  )}
                </button>
              </div>
            </div>

            {/* Login Button */}
            <button
              type="submit"
              className="w-full py-3.5 rounded-xl font-black text-[15px] flex items-center justify-center gap-2 transition-transform active:scale-98"
              style={{
                background: 'linear-gradient(135deg, #2A9D8F 0%, #1D7A6E 100%)',
                color: '#ffffff',
                boxShadow: '0 4px 12px rgba(42, 157, 143, 0.4), inset 0 1px 0 rgba(255,255,255,0.2)'
              }}
            >
              Se connecter
              <ArrowRight className="w-5 h-5" />
            </button>

            {/* Forgot Password */}
            <button
              type="button"
              className="w-full text-center mt-4 text-[12px] font-bold"
              style={{ color: '#2A9D8F' }}
            >
              Mot de passe oublié ?
            </button>
          </PaperPoster>
        </form>

        {/* Sign Up Section */}
        <div className="mt-4">
          <PaperPoster rotation={0.4} size="sm">
            <div className="text-center">
              <span className="text-[13px]" style={{ color: '#5A5A5A' }}>
                Pas encore de compte ?{' '}
              </span>
              <button
                className="text-[13px] font-bold"
                style={{ color: '#FFB800' }}
              >
                Créer un compte
              </button>
            </div>
          </PaperPoster>
        </div>

        {/* Stats Preview */}
        <div className="mt-6 grid grid-cols-3 gap-3">
          <PaperPoster rotation={-0.3} size="sm">
            <div className="text-center">
              <div className="text-[20px] font-black" style={{ 
                color: '#00D9B5',
                textShadow: '0 0 3px rgba(0,0,0,0.6), 0 0 5px rgba(0,0,0,0.4)'
              }}>
                50K+
              </div>
              <div className="text-[10px] font-medium" style={{ color: '#5A5A5A' }}>
                Utilisateurs
              </div>
            </div>
          </PaperPoster>

          <PaperPoster rotation={0.2} size="sm">
            <div className="text-center">
              <div className="text-[20px] font-black" style={{ 
                color: '#FFB800',
                textShadow: '0 0 3px rgba(0,0,0,0.6), 0 0 5px rgba(0,0,0,0.4)'
              }}>
                2.5M€
              </div>
              <div className="text-[10px] font-medium" style={{ color: '#5A5A5A' }}>
                Économisés
              </div>
            </div>
          </PaperPoster>

          <PaperPoster rotation={-0.2} size="sm">
            <div className="text-center">
              <div className="text-[20px] font-black" style={{ 
                color: '#A855F7',
                textShadow: '0 0 3px rgba(0,0,0,0.6), 0 0 5px rgba(0,0,0,0.4)'
              }}>
                98%
              </div>
              <div className="text-[10px] font-medium" style={{ color: '#5A5A5A' }}>
                Satisfaits
              </div>
            </div>
          </PaperPoster>
        </div>
      </div>
    </div>
  );
}