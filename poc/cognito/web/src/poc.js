import { Amplify } from 'aws-amplify';
import {
  signIn, confirmSignIn, signOut, fetchAuthSession, getCurrentUser,
  associateWebAuthnCredential, listWebAuthnCredentials, deleteWebAuthnCredential,
  setUpTOTP, verifyTOTPSetup, updateMFAPreference, fetchMFAPreference,
  updatePassword, updateUserAttributes, fetchUserAttributes,
} from 'aws-amplify/auth';
import {
  CognitoIdentityProviderClient, InitiateAuthCommand, RespondToAuthChallengeCommand,
  GetTokensFromRefreshTokenCommand, ChangePasswordCommand, UpdateUserAttributesCommand,
  GetUserAuthFactorsCommand,
} from '@aws-sdk/client-cognito-identity-provider';

let config;
let clientId;

const toErr = (e) => ({ error: e?.name || 'Error', message: String(e?.message || e), status: e?.$metadata?.httpStatusCode });
const wrap = (fn) => async (...args) => {
  try { return { ok: true, value: await fn(...args) }; } catch (e) { return { ok: false, ...toErr(e) }; }
};
const decode = (jwt) => JSON.parse(atob(jwt.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));

async function init(clientKey = 'WebClientId') {
  config = await (await fetch('/config.json')).json();
  clientId = config[clientKey];
  Amplify.configure({
    Auth: { Cognito: { userPoolId: config.UserPoolId, userPoolClientId: clientId, loginWith: { email: true } } },
  });
  return { clientKey };
}

const sdk = () => new CognitoIdentityProviderClient({ region: config.Region });
const commands = {
  InitiateAuth: InitiateAuthCommand,
  RespondToAuthChallenge: RespondToAuthChallengeCommand,
  GetTokensFromRefreshToken: GetTokensFromRefreshTokenCommand,
  ChangePassword: ChangePasswordCommand,
  UpdateUserAttributes: UpdateUserAttributesCommand,
  GetUserAuthFactors: GetUserAuthFactorsCommand,
};

async function sdkCall(name, input) {
  const out = await sdk().send(new commands[name]({ ClientId: clientId, ...input }));
  delete out.$metadata;
  return out;
}

async function tokens(forceRefresh = false) {
  const s = await fetchAuthSession({ forceRefresh });
  if (!s.tokens) return null;
  return {
    id: s.tokens.idToken?.toString(),
    access: s.tokens.accessToken?.toString(),
    idClaims: s.tokens.idToken?.payload,
    accessClaims: s.tokens.accessToken?.payload,
  };
}

function storageKeys() {
  return Object.keys(localStorage).filter((k) => k.startsWith('CognitoIdentityServiceProvider.'));
}

function refreshTokenFromStorage() {
  const prefix = `CognitoIdentityServiceProvider.${clientId}.`;
  const k = storageKeys().find((x) => x.startsWith(prefix) && x.endsWith('.refreshToken'));
  return k ? localStorage.getItem(k) : null;
}

// WebAuthn ceremony for the SDK-direct path (PoC 6)
const b64uToBuf = (s) => Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(s.length / 4) * 4, '=')), (c) => c.charCodeAt(0)).buffer;
const bufToB64u = (b) => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
async function webauthnGet(credentialRequestOptionsJson) {
  const o = JSON.parse(credentialRequestOptionsJson);
  const cred = await navigator.credentials.get({
    publicKey: {
      ...o,
      challenge: b64uToBuf(o.challenge),
      allowCredentials: (o.allowCredentials || []).map((c) => ({ ...c, id: b64uToBuf(c.id) })),
    },
  });
  return JSON.stringify({
    id: cred.id, rawId: bufToB64u(cred.rawId), type: cred.type,
    clientExtensionResults: cred.getClientExtensionResults(),
    authenticatorAttachment: cred.authenticatorAttachment,
    response: {
      clientDataJSON: bufToB64u(cred.response.clientDataJSON),
      authenticatorData: bufToB64u(cred.response.authenticatorData),
      signature: bufToB64u(cred.response.signature),
      userHandle: cred.response.userHandle ? bufToB64u(cred.response.userHandle) : undefined,
    },
  });
}

window.poc = {
  init,
  decode,
  storageKeys,
  refreshTokenFromStorage,
  signIn: wrap((input) => signIn(input)),
  confirmSignIn: wrap((input) => confirmSignIn(input)),
  signOut: wrap((input) => signOut(input)),
  getCurrentUser: wrap(() => getCurrentUser()),
  tokens: wrap(tokens),
  associateWebAuthnCredential: wrap(() => associateWebAuthnCredential()),
  listWebAuthnCredentials: wrap((input) => listWebAuthnCredentials(input)),
  deleteWebAuthnCredential: wrap((input) => deleteWebAuthnCredential(input)),
  setUpTOTP: wrap(async () => { const r = await setUpTOTP(); return { sharedSecret: r.sharedSecret }; }),
  verifyTOTPSetup: wrap((input) => verifyTOTPSetup(input)),
  updateMFAPreference: wrap((input) => updateMFAPreference(input)),
  fetchMFAPreference: wrap(() => fetchMFAPreference()),
  updatePassword: wrap((input) => updatePassword(input)),
  updateUserAttributes: wrap((input) => updateUserAttributes(input)),
  fetchUserAttributes: wrap(() => fetchUserAttributes()),
  sdk: wrap(sdkCall),
  webauthnGet: wrap(webauthnGet),
};
