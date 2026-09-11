import type { HTMLAttributes, ReactNode } from 'react'

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode
  padded?: boolean
}

/** Base surface container used across the terminal. */
export function Card({ children, padded = true, className = '', ...rest }: CardProps) {
  return (
    <div className={`card ${padded ? 'card-pad' : ''} ${className}`} {...rest}>
      {children}
    </div>
  )
}