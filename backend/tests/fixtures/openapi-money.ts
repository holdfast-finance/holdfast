import type { operations } from './schema'

type AmountOperation = operations['money']
type RequestAmount = AmountOperation['requestBody']['content']['application/json']['amount']
type ResponseAmount = AmountOperation['responses'][200]['content']['application/json']['amount']

type IsString<T> = (<U>() => U extends T ? 1 : 2) extends
  (<U>() => U extends string ? 1 : 2) ? true : false
type Assert<T extends true> = T

export type MoneyRequestIsString = Assert<IsString<RequestAmount>>
export type MoneyResponseIsString = Assert<IsString<ResponseAmount>>
