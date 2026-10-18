import { api } from "./api";

export interface LoginRequest {
  username: string;
  password: string;
  remember?: boolean;
}

export interface TokenResponse {
  access_token: string;
  token_type?: string;
}

export const login = (
  usernameOrParams: string | LoginRequest,
  password?: string,
  remember: boolean = false
) => {
  if (typeof usernameOrParams === "object") {
    return api.post<TokenResponse>("/auth/login", {
      username: usernameOrParams.username,
      password: usernameOrParams.password,
      remember: usernameOrParams.remember ?? false,
    });
  }
  return api.post<TokenResponse>("/auth/login", {
    username: usernameOrParams,
    password: password ?? "",
    remember,
  });
};
