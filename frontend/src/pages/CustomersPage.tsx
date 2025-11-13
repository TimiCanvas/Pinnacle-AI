import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import {
  ArrowLeft,
  Download,
  Search,
  Send,
  Users,
  AlertTriangle,
  CheckSquare,
  Square,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { useAudienceStore } from '@/store/audienceStore'
import { useRecommendationsQuery } from '@/services/recommendations'
import { SendOffersModal } from '@/components/SendOffersModal'
import { PRODUCT_CATEGORIES } from '@/data/productCategories'

const maskPhone = (id: string) => {
  const digits = id.replace(/\D/g, '')
  const sample = digits.padEnd(7, '0').slice(0, 7)
  return `+234${sample.slice(0, 3)}***${sample.slice(3)}`
}

export const CustomersPage = () => {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [searchTerm, setSearchTerm] = useState('')
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [modalScope, setModalScope] = useState<'page' | 'all'>('page')

  const {
    activeCategory,
    selectedProducts,
    filters,
    pagination,
    setPagination,
    search,
    setSearch,
    selectedCustomerIds,
    toggleCustomer,
    selectAllOnPage,
    clearSelections,
    selectAllAcrossPages,
    setSelectAllAcrossPages,
  } = useAudienceStore()

  const queryParams = useMemo(
    () => ({
      page: pagination.page,
      limit: pagination.limit,
      product_category: activeCategory,
      products: selectedProducts.length ? selectedProducts : undefined,
      min_age: filters.ageRange[0],
      max_age: filters.ageRange[1],
      state: filters.states.join(',') || undefined,
      account_type: filters.accountTypes.join(',') || undefined,
      status: filters.statuses.join(',') || undefined,
      min_confidence: filters.minConfidence,
      search: search || undefined,
    }),
    [pagination, activeCategory, selectedProducts, filters, search],
  )

  const { data, isLoading, isError } = useRecommendationsQuery(queryParams)

  const totalRecords = data?.pagination.total_records ?? 0
  const formattedTotal = totalRecords.toLocaleString()
  const pageStart = (pagination.page - 1) * pagination.limit + 1
  const pageEnd = Math.min(pageStart + (data?.data.length ?? 0) - 1, totalRecords)

  useEffect(() => {
    const debounce = setTimeout(() => {
      setSearch(searchTerm)
    }, 400)
    return () => clearTimeout(debounce)
  }, [searchTerm, setSearch])

  useEffect(() => {
    setSearchTerm(search)
  }, [search])

  const handleSelectAllPage = () => {
    if (!data) return
    setSelectAllAcrossPages(false)
    const ids = data.data.map((item) => item.customer_id)
    selectAllOnPage(ids)
  }

  const handleOpenModal = (scope: 'page' | 'all') => {
    setModalScope(scope)
    setIsModalOpen(true)
  }

  const appliedFiltersSummary = useMemo(() => {
    const parts = []
    if (selectedProducts.length) {
      parts.push(selectedProducts.join(' • '))
    } else {
      parts.push(activeCategory.replace('-', ' '))
    }
    parts.push(`Age ${filters.ageRange[0]}-${filters.ageRange[1]}`)
    if (filters.states.length) {
      parts.push(filters.states.join(' • '))
    }
    if (filters.statuses.length) {
      parts.push(filters.statuses.join(' • '))
    }
    return parts.join(' • ')
  }, [selectedProducts, filters, activeCategory])

  const pageSelectionCount = selectedCustomerIds.size
  const canSendPage = pageSelectionCount > 0
  const activeCategoryName = useMemo(
    () =>
      PRODUCT_CATEGORIES.find((category) => category.id === activeCategory)?.name ??
      activeCategory,
    [activeCategory],
  )

  return (
    <div className="min-h-screen bg-background pb-32 text-foreground">
      <header className="border-b border-border bg-white/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-col gap-4 px-6 py-8">
          <button
            type="button"
            onClick={() => navigate(-1)}
            className="flex items-center gap-2 text-sm font-semibold text-primary transition hover:text-primary/80"
          >
            <ArrowLeft className="h-4 w-4" />
            {t('categoryView.back')}
          </button>
          <div className="space-y-2">
            <h1 className="text-3xl font-semibold text-foreground">
              {t('customers.title')}
            </h1>
            <p className="text-sm text-muted">
              {t('customers.totalMatched', {
                count: totalRecords,
                formattedCount: formattedTotal,
              })}
            </p>
            <p className="text-xs font-semibold uppercase tracking-[0.3em] text-primary">
              {t('customers.appliedFilters', { filters: appliedFiltersSummary })}
            </p>
          </div>
        </div>
      </header>

        <main className="mx-auto mt-10 flex max-w-6xl flex-col gap-8 px-6">
          <section className="rounded-3xl border border-border bg-card p-6 shadow-subtle">
            <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
              <div className="flex w-full max-w-xl items-center gap-3 rounded-full border border-border px-4 py-2">
                <Search className="h-4 w-4 text-muted" />
                <Input
                  value={searchTerm}
                  onChange={(event) => setSearchTerm(event.target.value)}
                  placeholder={t('customers.searchPlaceholder')}
                  className="border-none bg-transparent focus-visible:ring-0"
                />
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <Button variant="outline" className="rounded-full border-border text-muted">
                  <Download className="mr-2 h-4 w-4" />
                  {t('customers.exportCurrent')}
                </Button>
                <Button variant="outline" className="rounded-full border-border text-muted">
                  <Download className="mr-2 h-4 w-4" />
                  {t('customers.exportAll')}
                </Button>
              </div>
            </div>
          </section>

        <section className="overflow-hidden rounded-3xl border border-border bg-card shadow-subtle">
          <div className="flex flex-col gap-3 border-b border-border bg-background/60 px-4 py-4 text-sm text-muted md:flex-row md:items-center md:justify-between md:px-6">
            <div className="flex flex-wrap items-center gap-3">
              <Button variant="ghost" className="rounded-full text-primary" onClick={handleSelectAllPage}>
                {t('customers.selectAllPage')}
              </Button>
              <Button
                variant="ghost"
                className="rounded-full text-primary"
                onClick={() => {
                  setSelectAllAcrossPages(true)
                  clearSelections()
                }}
              >
                {t('customers.selectAllResults', {
                  count: totalRecords,
                  formattedCount: formattedTotal,
                })}
              </Button>
              <Button
                variant="ghost"
                className="rounded-full text-muted"
                onClick={() => {
                  clearSelections()
                  setSelectAllAcrossPages(false)
                }}
              >
                {t('customers.bulk.deselectAll')}
              </Button>
            </div>
            <div className="flex items-center gap-2 text-xs text-muted">
              <Users className="h-4 w-4 text-primary" />
              {t('customers.bulk.selectedPage', {
                count: pageSelectionCount,
                page: pagination.page,
              })}
            </div>
          </div>
          <div className="hidden grid-cols-[40px_1.5fr_1fr_1fr_1fr_140px] gap-4 border-b border-border bg-background/70 px-6 py-4 text-xs font-semibold uppercase tracking-[0.3em] text-muted md:grid">
            <span />
            <span>{t('customers.tableHeaders.customer')}</span>
            <span>{t('customers.tableHeaders.city')}</span>
            <span>{t('customers.tableHeaders.product')}</span>
            <span>{t('customers.tableHeaders.confidence')}</span>
            <span>{t('customers.tableHeaders.actions')}</span>
          </div>

          <div className="divide-y divide-border">
            {isLoading && (
              <div className="flex items-center justify-center py-16 text-muted">
                Loading recommendations...
              </div>
            )}

            {isError && (
              <div className="flex items-center justify-center gap-2 py-16 text-primary">
                <AlertTriangle className="h-4 w-4" />
                Unable to load customer recommendations. Please try again.
              </div>
            )}

            {!isLoading &&
              !isError &&
              data?.data.map((customer) => {
                const isSelected = selectedCustomerIds.has(customer.customer_id)
                const confidencePercent = Math.round(customer.confidence_score * 100)
                return (
                  <div
                    key={customer.customer_id}
                    className="grid grid-cols-1 gap-4 px-4 py-6 text-sm text-foreground md:grid-cols-[40px_1.5fr_1fr_1fr_1fr_140px] md:items-center md:px-6"
                  >
                    <button
                      type="button"
                      onClick={() => toggleCustomer(customer.customer_id)}
                      className="flex items-center justify-center rounded-full border border-border p-2 text-primary transition hover:border-primary"
                    >
                      {isSelected ? (
                        <CheckSquare className="h-4 w-4" />
                      ) : (
                        <Square className="h-4 w-4" />
                      )}
                    </button>
                    <div className="space-y-2">
                      <p className="text-base font-semibold">{customer.customer_name}</p>
                      <div className="flex flex-wrap gap-2 text-xs text-muted">
                        <span>{customer.customer_id}</span>
                        <span>•</span>
                        <span>{maskPhone(customer.customer_id)}</span>
                      </div>
                    </div>
                    <div className="text-sm text-muted">
                      <p>{customer.city}</p>
                      <p className="text-xs">{customer.state}</p>
                    </div>
                    <div className="text-sm font-semibold text-foreground">
                      {customer.recommended_product}
                      <p className="text-xs text-muted">
                        {customer.occupation} • {customer.income_bracket} income
                      </p>
                    </div>
                    <div>
                      <div className="flex items-center justify-between text-xs font-semibold text-primary">
                        <span>{confidencePercent}%</span>
                      </div>
                      <div className="mt-2 h-2 rounded-full bg-border">
                        <div
                          className="h-2 rounded-full bg-primary"
                          style={{ width: `${confidencePercent}%` }}
                        />
                      </div>
                      <p className="mt-2 text-xs text-muted">
                        High engagement propensity detected
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      <Button variant="outline" className="w-full whitespace-nowrap rounded-full">
                        {t('customers.view')}
                      </Button>
                      <Button
                        variant="default"
                        className="w-full whitespace-nowrap rounded-full"
                        onClick={() => {
                          toggleCustomer(customer.customer_id)
                          handleOpenModal('page')
                        }}
                      >
                        {t('customers.sendOffer')}
                      </Button>
                    </div>
                  </div>
                )
              })}
          </div>
        </section>

        <section className="flex flex-col items-center justify-between gap-4 rounded-3xl border border-border bg-card p-6 text-sm text-muted shadow-subtle md:flex-row">
          <div>
            {t('customers.pagination.showing', {
              start: pageStart,
              end: pageEnd,
              total: totalRecords.toLocaleString(),
            })}
          </div>
          <div className="flex items-center gap-3">
            <span>{t('customers.pagination.rowsPerPage')}:</span>
            {[10, 25, 50, 100].map((size) => (
              <Button
                key={size}
                variant={pagination.limit === size ? 'default' : 'outline'}
                size="sm"
                className="rounded-full"
                onClick={() => setPagination({ limit: size, page: 1 })}
              >
                {size}
              </Button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPagination({ page: 1 })}
              disabled={pagination.page === 1}
            >
              {t('customers.pagination.first')}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPagination({ page: Math.max(1, pagination.page - 1) })}
              disabled={pagination.page === 1}
            >
              {t('customers.pagination.previous')}
            </Button>
            <Badge variant="neutral" className="px-4 py-2 text-sm">
              {pagination.page}
            </Badge>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                setPagination({
                  page: Math.min(data?.pagination.total_pages ?? pagination.page, pagination.page + 1),
                })
              }
              disabled={!data?.pagination.has_next}
            >
              {t('customers.pagination.next')}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                setPagination({
                  page: data?.pagination.total_pages ?? pagination.page,
                })
              }
              disabled={!data?.pagination.has_next}
            >
              {t('customers.pagination.last')}
            </Button>
          </div>
        </section>
      </main>

        {(pageSelectionCount > 0 || selectAllAcrossPages) && (
          <div className="fixed inset-x-0 bottom-0 z-50 bg-white/95 shadow-[0_-8px_30px_rgba(15,23,42,0.1)]">
            <div className="mx-auto flex max-w-6xl flex-col gap-4 px-6 py-4 md:flex-row md:items-center md:justify-between">
              <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                <Users className="h-4 w-4 text-primary" />
                {selectAllAcrossPages
                  ? t('customers.bulk.selectedAll', {
                      count: totalRecords,
                      formattedCount: formattedTotal,
                    })
                  : t('customers.bulk.selectedPage', {
                      count: pageSelectionCount,
                      page: pagination.page,
                    })}
              </div>
              <div className="flex flex-wrap items-center gap-3">
                {!selectAllAcrossPages && (
                  <Button
                    variant="ghost"
                    className="rounded-full text-primary"
                    onClick={() => {
                      setSelectAllAcrossPages(true)
                      clearSelections()
                    }}
                  >
                    {t('customers.bulk.selectAll', {
                      count: totalRecords,
                      formattedCount: formattedTotal,
                    })}
                  </Button>
                )}
                <Button
                  variant="ghost"
                  className="rounded-full text-muted"
                  onClick={() => {
                    clearSelections()
                    setSelectAllAcrossPages(false)
                  }}
                >
                  {t('customers.bulk.deselectAll')}
                </Button>
                <Button
                  variant="outline"
                  className="rounded-full border-border text-muted"
                  onClick={() => handleOpenModal(selectAllAcrossPages ? 'all' : 'page')}
                  disabled={!selectAllAcrossPages && !canSendPage}
                >
                  {selectAllAcrossPages
                    ? t('customers.bulk.viewSample')
                    : t('customers.bulk.sendToSelected', { count: pageSelectionCount })}
                </Button>
                <Button
                  className="rounded-full bg-primary px-8 py-3 text-sm font-semibold uppercase tracking-wide"
                  onClick={() => handleOpenModal(selectAllAcrossPages ? 'all' : 'page')}
                  disabled={!selectAllAcrossPages && !canSendPage}
                >
                  <Send className="mr-2 h-4 w-4" />
                  {selectAllAcrossPages
                    ? t('customers.bulk.sendToAll', {
                        count: totalRecords,
                        formattedCount: formattedTotal,
                      })
                    : t('customers.bulk.sendToSelected', { count: pageSelectionCount })}
                </Button>
              </div>
            </div>
          </div>
        )}

      <SendOffersModal
        open={isModalOpen}
        onOpenChange={setIsModalOpen}
        selectedCount={
          modalScope === 'all' ? totalRecords : Math.max(pageSelectionCount, 1)
        }
        totalCount={totalRecords}
        products={selectedProducts.length ? selectedProducts : [activeCategoryName]}
        scope={modalScope}
        currentPage={pagination.page}
      />
    </div>
  )
}
