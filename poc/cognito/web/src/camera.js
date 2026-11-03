import { Amplify } from 'aws-amplify';
import { fetchAuthSession, getCurrentUser } from 'aws-amplify/auth';

window.camera = {
  async check() {
    const config = await (await fetch('/config.json')).json();
    Amplify.configure({
      Auth: { Cognito: { userPoolId: config.UserPoolId, userPoolClientId: config.WebClientId, loginWith: { email: true } } },
    });
    const out = { href: location.href };
    try {
      const u = await getCurrentUser();
      out.user = { userId: u.userId, username: u.username };
    } catch (e) { out.userError = `${e.name}: ${e.message}`; }
    try {
      const s = await fetchAuthSession();
      out.hasIdToken = !!s.tokens?.idToken;
      out.hasAccessToken = !!s.tokens?.accessToken;
      out.sub = s.tokens?.idToken?.payload?.sub;
    } catch (e) { out.sessionError = `${e.name}: ${e.message}`; }
    return out;
  },
};
