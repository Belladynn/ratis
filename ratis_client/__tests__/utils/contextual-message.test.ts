import { getContextualMessage } from '../../utils/contextual-message';

type Mission = { id: number; status: string };

const partial: Mission[] = [
  { id: 1, status: 'completed' },
  { id: 2, status: 'active' },
];
const allDone: Mission[] = partial.map(m => ({ ...m, status: 'completed' }));

describe('getContextualMessage', () => {
  it('streak >= 7 takes highest priority', () => {
    expect(getContextualMessage({ hour: 14, streak: 7, missions: partial }))
      .toBe("T'es en feu ! 🔥");
  });

  it('all missions done — second priority (streak < 7)', () => {
    expect(getContextualMessage({ hour: 14, streak: 3, missions: allDone }))
      .toBe('GG, toutes les missions ! 💪');
  });

  it('morning: hours 6–11', () => {
    expect(getContextualMessage({ hour: 6,  streak: 3, missions: partial })).toBe('Prêt pour économiser ? 😎');
    expect(getContextualMessage({ hour: 11, streak: 3, missions: partial })).toBe('Prêt pour économiser ? 😎');
  });

  it('afternoon: hours 12–17', () => {
    expect(getContextualMessage({ hour: 12, streak: 3, missions: partial })).toBe('Bon plan de la journée ? 🛒');
    expect(getContextualMessage({ hour: 17, streak: 3, missions: partial })).toBe('Bon plan de la journée ? 🛒');
  });

  it('evening: hours 18–21', () => {
    expect(getContextualMessage({ hour: 18, streak: 3, missions: partial })).toBe('Soirée shopping ! 🌃');
    expect(getContextualMessage({ hour: 21, streak: 3, missions: partial })).toBe('Soirée shopping ! 🌃');
  });

  it('night: hours 0–5 and 22–23', () => {
    expect(getContextualMessage({ hour: 0,  streak: 3, missions: partial })).toBe('Mode nocturne activé 🌙');
    expect(getContextualMessage({ hour: 23, streak: 3, missions: partial })).toBe('Mode nocturne activé 🌙');
  });

  it('empty missions array is treated as all done', () => {
    expect(getContextualMessage({ hour: 14, streak: 3, missions: [] }))
      .toBe('GG, toutes les missions ! 💪');
  });
});
