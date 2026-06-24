type Mission = { status: string };

interface Params {
  hour: number;
  streak: number;
  missions: Mission[];
}

export function getContextualMessage({ hour, streak, missions }: Params): string {
  const completedCount = missions.filter(m => m.status !== 'active').length;
  if (streak >= 7)                        return "T'es en feu ! 🔥";
  if (completedCount === missions.length) return 'GG, toutes les missions ! 💪';
  if (hour >= 6  && hour < 12)           return 'Prêt pour économiser ? 😎';
  if (hour >= 12 && hour < 18)           return 'Bon plan de la journée ? 🛒';
  if (hour >= 18 && hour < 22)           return 'Soirée shopping ! 🌃';
  return 'Mode nocturne activé 🌙';
}
