import { Zap, Target, TrendingUp, Flame, Trophy, Coins, ChevronRight, Gift, ShoppingBag, Check, Map } from 'lucide-react';
import { PaperPoster } from './PaperPoster';

export function Dashboard() {
  const missions = [
    { id: 1, label: "Scanner 3 produits", points: 50, completed: true },
    { id: 2, label: "Comparer 5 prix", points: 30, completed: true },
    { id: 3, label: "Ajouter une liste", points: 20, completed: false },
  ];

  const spendingCategories = [
    { name: "Food", amount: 145.50, color: "#FF6B35", percentage: 38, emoji: "🍕" },
    { name: "Transport", amount: 89.20, color: "#00D9B5", percentage: 23, emoji: "🚗" },
    { name: "Supermarché", amount: 102.80, color: "#A855F7", percentage: 27, emoji: "🛒" },
    { name: "Autres", amount: 45.30, color: "#FFB800", percentage: 12, emoji: "🎯" },
  ];

  const stores = [
    { name: "Carrefour", cost: 142.50, savings: 8.20 },
    { name: "Leclerc", cost: 138.30, savings: 12.40 },
    { name: "Auchan", cost: 145.80, savings: 4.90 },
  ];

  const totalSpending = spendingCategories.reduce((sum, cat) => sum + cat.amount, 0);
  const roiEarned = 2.53;
  const roiPotential = 7.99;
  const roiPercentage = Math.round((roiEarned / roiPotential) * 100);
  const currentStreak = 7;
  const cabecoins = 1250;
  const seasonProgress = 67;
  const seasonLevel = 12;

  const getContextualMessage = () => {
    const hour = new Date().getHours();
    const completedMissions = missions.filter(m => m.completed).length;
    
    if (currentStreak >= 7) {
      return "T'es en feu ! 🔥";
    } else if (completedMissions === missions.length) {
      return "GG, toutes les missions ! 💪";
    } else if (hour >= 6 && hour < 12) {
      return "Prêt pour économiser ? 😎";
    } else if (hour >= 12 && hour < 18) {
      return "Bon plan de la journée ? 🛒";
    } else if (hour >= 18 && hour < 22) {
      return "Soirée shopping ! 🌃";
    } else {
      return "Mode nocturne activé 🌙";
    }
  };

  return (
    <div className="min-h-full pb-6" style={{ background: 'transparent' }}>
      {/* Header avec Ratis */}
      <div className="px-4 pt-4 mb-3">
        <PaperPoster rotation={0.3} size="md">
          <div className="flex items-start gap-3">
            <div className="relative">
              <img 
                src="/imports/image.png" 
                alt="Ratis"
                className="w-16 h-16 object-contain"
                style={{
                  filter: 'drop-shadow(0 2px 8px rgba(0,0,0,0.2))'
                }}
              />
            </div>
            
            <div className="flex-1 mt-1">
              <div 
                className="relative px-3 py-2 rounded-lg rounded-tl-sm inline-block"
                style={{
                  background: '#FFFFFF',
                  border: '2px solid #2A2A2A',
                  boxShadow: '0 2px 4px rgba(0, 0, 0, 0.1)'
                }}
              >
                <div style={{ color: '#2A2A2A' }} className="text-[13px] font-bold">
                  {getContextualMessage()}
                </div>
                
                <div 
                  className="absolute left-0 top-0 w-0 h-0"
                  style={{
                    transform: 'translate(-6px, 8px)',
                    borderTop: '6px solid transparent',
                    borderBottom: '6px solid transparent',
                    borderRight: '6px solid #2A2A2A',
                  }}
                />
                <div 
                  className="absolute left-0 top-0 w-0 h-0"
                  style={{
                    transform: 'translate(-4px, 9px)',
                    borderTop: '5px solid transparent',
                    borderBottom: '5px solid transparent',
                    borderRight: '5px solid #FFFFFF',
                  }}
                />
              </div>
              <div className="text-[9px] font-bold mt-1 ml-1" style={{ color: '#FFB800' }}>
                — Ratis, le rat malin
              </div>
            </div>
          </div>
        </PaperPoster>
      </div>

      {/* Stats compactes */}
      <div className="px-4 mb-3">
        <PaperPoster rotation={-0.2} size="sm">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <div 
                className="w-8 h-8 rounded-lg flex items-center justify-center"
                style={{
                  background: 'linear-gradient(135deg, #FF6B35, #E63E11)',
                }}
              >
                <Flame className="w-4 h-4 text-white" />
              </div>
              <div>
                <div style={{ color: '#2A2A2A' }} className="font-bold text-[13px]">{currentStreak}</div>
                <div className="text-[9px] font-medium" style={{ color: '#5A5A5A' }}>jours</div>
              </div>
            </div>

            <div className="w-px h-8" style={{ background: 'rgba(0,0,0,0.1)' }} />

            <div className="flex items-center gap-2 flex-1">
              <div 
                className="w-8 h-8 rounded-lg flex items-center justify-center"
                style={{
                  background: 'linear-gradient(135deg, #A855F7, #7C3BAD)',
                }}
              >
                <Trophy className="w-4 h-4 text-white" />
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-1 mb-1">
                  <span style={{ color: '#2A2A2A' }} className="font-bold text-[11px]">Saison 1</span>
                  <span className="text-[10px] font-bold" style={{ color: '#A855F7' }}>Niv. {seasonLevel}</span>
                </div>
                <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'rgba(0,0,0,0.1)' }}>
                  <div 
                    className="h-full rounded-full"
                    style={{
                      width: `${seasonProgress}%`,
                      background: 'linear-gradient(90deg, #A855F7, #EC4899)',
                    }}
                  />
                </div>
              </div>
            </div>

            <div className="w-px h-8" style={{ background: 'rgba(0,0,0,0.1)' }} />

            <div className="flex items-center gap-2">
              <div 
                className="w-8 h-8 rounded-lg flex items-center justify-center relative"
                style={{
                  background: '#FFB800',
                  boxShadow: 'inset 0 -2px 0 rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.3)'
                }}
              >
                <Coins className="w-4 h-4" style={{ color: '#1A1F2E' }} />
              </div>
              <div className="font-black text-[15px]" style={{ color: '#FFB800' }}>
                {cabecoins.toLocaleString()}
              </div>
            </div>
          </div>
        </PaperPoster>
      </div>

      {/* Missions + ROI */}
      <div className="px-4 mb-3">
        <PaperPoster rotation={0.4} size="md">
          <div className="flex gap-4">
            <div className="flex-1">
              <div className="flex items-center gap-2 mb-3">
                <Target className="w-4 h-4" style={{ color: '#00D9B5' }} />
                <h3 style={{ color: '#2A2A2A' }} className="text-[14px] font-bold">Missions du jour</h3>
                <span className="text-[11px] font-bold ml-auto" style={{ color: '#00D9B5' }}>
                  {missions.filter(m => m.completed).length}/{missions.length}
                </span>
              </div>
              
              <div className="space-y-2.5">
                {missions.map((mission) => (
                  <div key={mission.id} className="flex items-center gap-2">
                    <div 
                      className="w-4 h-4 rounded flex items-center justify-center shrink-0"
                      style={{ 
                        background: mission.completed ? '#10B981' : 'transparent',
                        border: mission.completed ? 'none' : '2px solid #94A3B8',
                      }}
                    >
                      {mission.completed && (
                        <Check className="w-3 h-3 text-white" strokeWidth={3} />
                      )}
                    </div>
                    
                    <div className="flex-1 min-w-0">
                      <div 
                        className="text-[12px]"
                        style={{ color: mission.completed ? '#5A5A5A' : '#2A2A2A' }}
                      >
                        {mission.label}
                      </div>
                    </div>
                    
                    <div 
                      className="px-2 py-0.5 rounded"
                      style={{ 
                        background: '#FFB800',
                        boxShadow: 'inset 0 -1px 0 rgba(0,0,0,0.2)'
                      }}
                    >
                      <span className="text-[10px] font-black" style={{ color: '#1A1F2E' }}>
                        +{mission.points}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="w-px" style={{ background: 'rgba(0,0,0,0.1)' }} />

            <div className="flex flex-col items-center justify-center" style={{ width: '100px' }}>
              <div className="flex items-center gap-1 mb-2">
                <Zap className="w-3.5 h-3.5" style={{ color: '#FFB800' }} />
                <span style={{ color: '#2A2A2A' }} className="text-[11px] font-bold">ROI</span>
              </div>

              <div className="relative w-[70px] h-[70px] mb-2">
                <svg className="w-[70px] h-[70px] -rotate-90" viewBox="0 0 70 70">
                  <circle cx="35" cy="35" r="28" fill="none" stroke="rgba(0,0,0,0.1)" strokeWidth="8" />
                  <circle
                    cx="35" cy="35" r="28" fill="none" stroke="#FFB800" strokeWidth="8"
                    strokeDasharray={`${2 * Math.PI * 28 * (roiPercentage / 100)} ${2 * Math.PI * 28}`}
                    strokeLinecap="round"
                  />
                </svg>
                
                <div className="absolute inset-0 flex flex-col items-center justify-center">
                  <div style={{ color: '#2A2A2A' }} className="text-[18px] font-black">{roiPercentage}%</div>
                </div>
              </div>
              
              <div className="text-center">
                <div className="text-[10px] font-bold" style={{ color: '#FFB800' }}>
                  {roiEarned.toFixed(2)}€ / {roiPotential.toFixed(2)}€
                </div>
              </div>
            </div>
          </div>
        </PaperPoster>
      </div>

      {/* Categories */}
      <div className="px-4 mb-3">
        <PaperPoster rotation={-0.3} size="md">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <TrendingUp className="w-4 h-4" style={{ color: '#EC4899' }} />
              <h3 style={{ color: '#2A2A2A' }} className="text-[14px] font-bold">Dépenses</h3>
            </div>
            <div className="px-2.5 py-1 rounded-lg text-[11px] font-bold" style={{ background: 'rgba(0,0,0,0.05)', color: '#EC4899' }}>
              Avril 2026
            </div>
          </div>

          <div className="space-y-2.5">
            {spendingCategories.map((category) => (
              <div key={category.name} className="flex items-center gap-3">
                <div 
                  className="w-8 h-8 rounded-lg flex items-center justify-center text-[16px]"
                  style={{
                    background: `${category.color}20`,
                    border: `1px solid ${category.color}40`
                  }}
                >
                  {category.emoji}
                </div>
                
                <div className="flex-1">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[12px] font-medium" style={{ color: '#5A5A5A' }}>
                      {category.name}
                    </span>
                    <div className="flex items-baseline gap-1.5">
                      <span style={{ color: '#2A2A2A' }} className="text-[13px] font-bold">
                        {category.amount.toFixed(0)}€
                      </span>
                      <span className="text-[10px] font-bold" style={{ color: category.color }}>
                        {category.percentage}%
                      </span>
                    </div>
                  </div>
                  <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'rgba(0,0,0,0.1)' }}>
                    <div 
                      className="h-full rounded-full"
                      style={{
                        width: `${category.percentage}%`,
                        background: category.color,
                      }}
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className="mt-3 pt-3" style={{ borderTop: '1px solid rgba(0,0,0,0.1)' }}>
            <div className="flex items-center justify-between">
              <span className="text-[12px] font-medium" style={{ color: '#5A5A5A' }}>Total du mois</span>
              <span style={{ color: '#2A2A2A' }} className="text-[16px] font-black">{totalSpending.toFixed(0)}€</span>
            </div>
          </div>
        </PaperPoster>
      </div>

      {/* Stores */}
      <div className="px-4 mb-3">
        <PaperPoster rotation={0.2} size="md">
          <div className="absolute top-2 right-2 opacity-5">
            <Map className="w-20 h-20" style={{ color: '#10B981' }} />
          </div>

          <div className="relative z-10">
            <div className="flex items-center gap-2 mb-3">
              <ShoppingBag className="w-4 h-4" style={{ color: '#10B981' }} />
              <h3 style={{ color: '#2A2A2A' }} className="text-[14px] font-bold">Meilleurs magasins</h3>
            </div>

            <div className="space-y-2">
              {stores.map((store, index) => (
                <div 
                  key={store.name} 
                  className="flex items-center justify-between py-2 px-3 rounded-lg"
                  style={{ 
                    background: index === 0 ? 'rgba(16, 185, 129, 0.1)' : 'transparent',
                    border: index === 0 ? '1px solid rgba(16, 185, 129, 0.3)' : '1px solid transparent'
                  }}
                >
                  <div className="flex items-center gap-2.5">
                    <div 
                      className="w-6 h-6 rounded-lg flex items-center justify-center text-[12px] font-black"
                      style={{ 
                        background: index === 0 ? '#10B981' : 'rgba(0,0,0,0.08)',
                        color: index === 0 ? '#ffffff' : '#5A5A5A',
                      }}
                    >
                      {index + 1}
                    </div>
                    <span className="text-[13px] font-medium" style={{ color: '#2A2A2A' }}>
                      {store.name}
                    </span>
                  </div>
                  
                  <div className="flex items-baseline gap-2">
                    <span style={{ color: '#2A2A2A' }} className="text-[13px] font-bold">
                      {store.cost.toFixed(2)}€
                    </span>
                    <span className="text-[11px] font-black" style={{ color: '#10B981' }}>
                      -{store.savings.toFixed(2)}€
                    </span>
                  </div>
                </div>
              ))}
            </div>

            <button 
              className="w-full mt-2 py-2 rounded-lg flex items-center justify-center gap-1"
              style={{
                background: 'rgba(16, 185, 129, 0.1)',
                border: '1px solid rgba(16, 185, 129, 0.3)'
              }}
            >
              <span className="text-[12px] font-bold" style={{ color: '#10B981' }}>
                Voir l'itinéraire optimal
              </span>
              <ChevronRight className="w-4 h-4" style={{ color: '#10B981' }} />
            </button>
          </div>
        </PaperPoster>
      </div>

      {/* Daily Bonus */}
      <div className="px-4">
        <PaperPoster rotation={-0.4} size="md">
          <div 
            className="rounded-lg p-3 relative overflow-hidden"
            style={{
              background: 'linear-gradient(135deg, #A855F7 0%, #EC4899 100%)',
            }}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div 
                  className="w-10 h-10 rounded-lg flex items-center justify-center"
                  style={{
                    background: 'rgba(255, 255, 255, 0.2)',
                  }}
                >
                  <Gift className="w-5 h-5 text-white" />
                </div>
                <div>
                  <div className="text-white font-bold text-[13px]">Bonus quotidien</div>
                  <div className="text-white/80 text-[11px]">Reviens demain pour +200 coins</div>
                </div>
              </div>
              <ChevronRight className="w-5 h-5 text-white/80" />
            </div>
          </div>
        </PaperPoster>
      </div>
    </div>
  );
}
