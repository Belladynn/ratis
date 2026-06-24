// ratis_client/__tests__/services/rewards-client-achievements.test.ts
//
// Achievements V1 — `triggerSecretEvent` thin wrapper contract.
import { rewardsClient, triggerSecretEvent } from '@/services/rewards-client';

describe('triggerSecretEvent', () => {
  beforeEach(() => {
    jest.spyOn(rewardsClient, 'post').mockReset();
  });

  it('POSTs the konami event payload', async () => {
    const spy = jest
      .spyOn(rewardsClient, 'post')
      .mockResolvedValue({ ok: true, unlocked_count: 1 });
    const out = await triggerSecretEvent('konami_code_entered');
    expect(spy).toHaveBeenCalledWith(
      '/rewards/achievements/secret-event',
      { event: 'konami_code_entered' },
    );
    expect(out.unlocked_count).toBe(1);
  });

  it('POSTs the 3am event payload', async () => {
    const spy = jest
      .spyOn(rewardsClient, 'post')
      .mockResolvedValue({ ok: true, unlocked_count: 0 });
    await triggerSecretEvent('app_opened_at_3am');
    expect(spy).toHaveBeenCalledWith(
      '/rewards/achievements/secret-event',
      { event: 'app_opened_at_3am' },
    );
  });
});
