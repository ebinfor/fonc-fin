/**
 * FONCIER+ v3.4.7 — AuthContext
 */
import React, { createContext, useContext, useState, ReactNode } from 'react'
import { login as mockLogin } from '../services/mockBackend'

interface User {
  id: string
  name: string
  role: string
  email: string
  region: string
  access_token: string
}

interface AuthContextType {
  user: User | null
  login: (email: string, password: string) => boolean
  logout: () => void
}

const AuthContext = createContext<AuthContextType | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(() => {
    try {
      const stored = sessionStorage.getItem('foncier_user')
      return stored ? JSON.parse(stored) : null
    } catch { return null }
  })

  const login = (email: string, password: string): boolean => {
    const result = mockLogin(email, password)
    if (!result) return false
    const u = result as User
    setUser(u)
    sessionStorage.setItem('foncier_user', JSON.stringify(u))
    return true
  }

  const logout = () => {
    setUser(null)
    sessionStorage.removeItem('foncier_user')
  }

  return (
    <AuthContext.Provider value={{ user, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
