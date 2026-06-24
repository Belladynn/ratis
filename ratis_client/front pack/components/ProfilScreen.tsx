import { User, Trophy, Target, TrendingUp, Settings, LogOut, Star, Flame, Coins, Award } from 'lucide-react';
import { PaperPoster } from './PaperPoster';

export function ProfilScreen() {
  const userData = {
    name: 'Alex Martin',
    email: 'alex.martin@email.com',
    memberSince: 'Janvier 2026',
    avatar: '🦝'
  };

  const stats = [
    { label: 'Cabecoins', value: '1,250', icon: Coins, color: '#FFB800' },
    { label: 'Économisé', value: '145€', icon: TrendingUp, color: '#2A9D8F' },
    { label: 'Série', value: '7 jours', icon: Flame, color: '#FF6B35' },
    { label: 'Missions', value: '42/50', icon: Target, color: '#A855F7' },
  ];

  const achievements = [
    { id: 1, name: 'Premier scan', icon: '🎯', unlocked: true, color: '#2A9D8F' },
    { id: 2, name: 'Série de 7 jours', icon: '🔥', unlocked: true, color: '#FF6B35' },
    { id: 3, name: '100€ économisés', icon: '💰', unlocked: true, color: '#FFB800' },
    { id: 4, name: 'Expert Ratis', icon: '⭐', unlocked: false, color: '#94A3B8' },
    { id: 5, name: 'Chasseur de prix', icon: '🏆', unlocked: false, color: '#94A3B8' },
    { id: 6, name: 'Maître économe', icon: '👑', unlocked: false, color: '#94A3B8' },
  ];

  return (
    <div className="min-h-full pb-6" style={{ background: 'transparent' }}>
      {/* Profile Header */}
      <div className="px-4 pt-4 mb-4">
        <PaperPoster rotation={-0.4} size="lg">
          <div className="flex items-center gap-4">
            {/* Avatar */}
            <div 
              className="w-20 h-20 rounded-full flex items-center justify-center text-[40px]"
              style={{
                background: 'linear-gradient(135deg, #2A9D8F 0%, #1e7a6f 100%)',
                boxShadow: '0 4px 12px rgba(42, 157, 143, 0.4)'
              }}
            >
              {userData.avatar}
            </div>

            {/* User Info */}
            <div className="flex-1">
              <h2 className="text-[#2A2A2A] text-[20px] font-bold mb-1">
                {userData.name}
              </h2>
              <p className="text-[12px] mb-1" style={{ color: '#5A5A5A' }}>
                {userData.email}
              </p>
              <div className="flex items-center gap-1.5">
                <Star className="w-3.5 h-3.5" style={{ color: '#FFB800' }} fill="#FFB800" />
                <span className="text-[11px] font-bold" style={{ color: '#5A5A5A' }}>
                  Membre depuis {userData.memberSince}
                </span>
              </div>
            </div>
          </div>
        </PaperPoster>
      </div>

      {/* Stats Grid */}
      <div className="px-4 mb-4">
        <h3 className="text-white text-[14px] font-bold mb-3 px-1" style={{ textShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>
          Mes statistiques
        </h3>
        <div className="grid grid-cols-2 gap-3">
          {stats.map((stat, index) => {
            const Icon = stat.icon;
            return (
              <PaperPoster 
                key={stat.label}
                rotation={index % 2 === 0 ? 0.3 : -0.3}
                size="sm"
              >
                <div className="text-center">
                  <div 
                    className="w-10 h-10 rounded-full flex items-center justify-center mx-auto mb-2"
                    style={{
                      background: `${stat.color}30`,
                      border: `2px solid ${stat.color}`
                    }}
                  >
                    <Icon className="w-5 h-5" style={{ 
                      color: stat.color,
                      filter: 'drop-shadow(0 0 2px rgba(0,0,0,0.6)) drop-shadow(0 0 3px rgba(0,0,0,0.5))'
                    }} />
                  </div>
                  <div className="text-[#2A2A2A] text-[18px] font-black">
                    {stat.value}
                  </div>
                  <div className="text-[11px] mt-0.5" style={{ color: '#5A5A5A' }}>
                    {stat.label}
                  </div>
                </div>
              </PaperPoster>
            );
          })}
        </div>
      </div>

      {/* Achievements */}
      <div className="px-4 mb-4">
        <div className="flex items-center gap-2 mb-3 px-1">
          <h3 className="text-white text-[14px] font-bold" style={{ textShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>
            Succès
          </h3>
          <div 
            className="px-2 py-0.5 rounded-full text-[11px] font-bold"
            style={{ 
              background: 'rgba(212, 175, 135, 0.3)',
              border: '1px solid rgba(212, 175, 135, 0.5)',
              color: '#2A9D8F'
            }}
          >
            {achievements.filter(a => a.unlocked).length}/{achievements.length}
          </div>
        </div>

        <div className="grid grid-cols-3 gap-2">
          {achievements.map((achievement, index) => (
            <PaperPoster 
              key={achievement.id}
              rotation={index % 3 === 0 ? 0.4 : index % 3 === 1 ? -0.4 : 0.2}
              size="sm"
            >
              <div className="text-center">
                <div 
                  className="w-12 h-12 rounded-full flex items-center justify-center mx-auto mb-2 text-[24px]"
                  style={{
                    background: achievement.unlocked ? `${achievement.color}30` : 'rgba(0,0,0,0.1)',
                    border: `2px solid ${achievement.color}`,
                    opacity: achievement.unlocked ? 1 : 0.5
                  }}
                >
                  {achievement.icon}
                </div>
                <div className="text-[10px] font-bold" style={{ color: achievement.unlocked ? '#2A2A2A' : '#94A3B8' }}>
                  {achievement.name}
                </div>
              </div>
            </PaperPoster>
          ))}
        </div>
      </div>

      {/* Level Progress */}
      <div className="px-4 mb-4">
        <h3 className="text-white text-[14px] font-bold mb-3 px-1" style={{ textShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>
          Niveau Ratis
        </h3>
        <PaperPoster rotation={-0.5} size="md">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-3">
              <div 
                className="w-12 h-12 rounded-full flex items-center justify-center"
                style={{
                  background: 'linear-gradient(135deg, #A855F7 0%, #7C3BAD 100%)',
                  boxShadow: '0 4px 12px rgba(168, 85, 247, 0.4)'
                }}
              >
                <Trophy className="w-6 h-6 text-white" />
              </div>
              <div>
                <div className="text-[#2A2A2A] text-[16px] font-bold">Niveau 12</div>
                <div className="text-[11px]" style={{ color: '#5A5A5A' }}>Rat Expérimenté</div>
              </div>
            </div>
            <div className="text-right">
              <div className="text-[#2A2A2A] text-[14px] font-bold">2,340 XP</div>
              <div className="text-[10px]" style={{ color: '#5A5A5A' }}>/ 3,000 XP</div>
            </div>
          </div>
          <div className="h-2.5 rounded-full overflow-hidden" style={{ background: 'rgba(0,0,0,0.15)' }}>
            <div 
              className="h-full rounded-full"
              style={{
                width: '78%',
                background: 'linear-gradient(90deg, #A855F7 0%, #EC4899 100%)',
                boxShadow: '0 0 8px rgba(168, 85, 247, 0.6)'
              }}
            />
          </div>
          <p className="text-[10px] mt-2 text-center" style={{ color: '#5A5A5A' }}>
            Plus que 660 XP pour le niveau 13 !
          </p>
        </PaperPoster>
      </div>

      {/* Actions */}
      <div className="px-4 space-y-3">
        <PaperPoster rotation={0.3} size="sm">
          <button className="w-full flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div 
                className="w-9 h-9 rounded-full flex items-center justify-center"
                style={{
                  background: 'rgba(42, 157, 143, 0.2)',
                  border: '1px solid rgba(42, 157, 143, 0.4)'
                }}
              >
                <Settings className="w-5 h-5" style={{ color: '#2A9D8F' }} />
              </div>
              <span className="text-[#2A2A2A] text-[14px] font-bold">Paramètres</span>
            </div>
            <div className="text-[#5A5A5A]">›</div>
          </button>
        </PaperPoster>

        <PaperPoster rotation={-0.4} size="sm">
          <button className="w-full flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div 
                className="w-9 h-9 rounded-full flex items-center justify-center"
                style={{
                  background: 'rgba(244, 162, 97, 0.2)',
                  border: '1px solid rgba(244, 162, 97, 0.4)'
                }}
              >
                <Award className="w-5 h-5" style={{ color: '#F4A261' }} />
              </div>
              <span className="text-[#2A2A2A] text-[14px] font-bold">Mes récompenses</span>
            </div>
            <div className="text-[#5A5A5A]">›</div>
          </button>
        </PaperPoster>

        <PaperPoster rotation={0.2} size="sm">
          <button className="w-full flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div 
                className="w-9 h-9 rounded-full flex items-center justify-center"
                style={{
                  background: 'rgba(231, 111, 81, 0.2)',
                  border: '1px solid rgba(231, 111, 81, 0.4)'
                }}
              >
                <LogOut className="w-5 h-5" style={{ color: '#E76F51' }} />
              </div>
              <span className="text-[#2A2A2A] text-[14px] font-bold">Déconnexion</span>
            </div>
            <div className="text-[#5A5A5A]">›</div>
          </button>
        </PaperPoster>
      </div>
    </div>
  );
}