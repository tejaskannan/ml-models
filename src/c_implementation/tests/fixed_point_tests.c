#include "fixed_point_tests.h"


int main(void) {
    // Run all tests
    test_mul_basic();
    test_div_basic();
    test_exp_basic();
    test_exp_neg();
    test_tanh_basic();
    test_sigmoid_basic();

    printf("\nPassed All Tests.\n\n");
}

void test_mul_basic(void) {
    int fixed_point_bits = 3;
    int16_t one = 1 << fixed_point_bits;
    assert(one == fp_mul(one, one, fixed_point_bits));
    assert(fp_neg(one) == fp_mul(fp_neg(one), one, fixed_point_bits));

    int16_t two = 1 << (fixed_point_bits + 1);
    assert(two == fp_mul(one, two, fixed_point_bits));
    assert(two == fp_mul(two, one, fixed_point_bits));

    int16_t four = 1 << (fixed_point_bits + 2);
    assert(four == fp_mul(two, two, fixed_point_bits));
}

void test_div_basic(void) {
    int fixed_point_bits = 3;
    int16_t one = 1 << fixed_point_bits;
    assert(one == fp_div(one, one, fixed_point_bits));
    assert(fp_neg(one) == fp_div(fp_neg(one), one, fixed_point_bits));

    int16_t two = 1 << (fixed_point_bits + 1);
    int16_t one_half = 1 << (fixed_point_bits - 1);
    assert(one_half == fp_div(one, two, fixed_point_bits));
    assert(two == fp_div(two, one, fixed_point_bits));

    int16_t four = 1 << (fixed_point_bits + 2);
    assert(two == fp_div(four, two, fixed_point_bits));
    assert(one == fp_div(two, two, fixed_point_bits));
}

void test_exp_basic(void) {
    int fixed_point_bits = 5;
    int16_t one = 1 << fixed_point_bits;
    int16_t two = 1 << (fixed_point_bits + 1);

    assert(86 == fp_exp(one, fixed_point_bits));
    assert(233 == fp_exp(two, fixed_point_bits));
}


void test_exp_neg(void) {
    int fixed_point_bits = 8;
    int16_t one = 1 << fixed_point_bits; 
    int16_t two = 1 << (fixed_point_bits + 1);

    assert(95 == fp_exp(fp_neg(one), fixed_point_bits));
    assert(43 == fp_exp(fp_neg(two), fixed_point_bits));
}


void test_tanh_basic(void) {
    int fixed_point_bits = 5;
    int16_t zero = 0;
    int16_t one = 1 << fixed_point_bits;
    int16_t two = 1 << (fixed_point_bits + 1);

    assert(0 == fp_tanh(zero, fixed_point_bits));
    assert(24 == fp_tanh(one, fixed_point_bits));
    assert(-24 == fp_tanh(fp_neg(one), fixed_point_bits));
    assert(32 == fp_tanh(two, fixed_point_bits));
    assert(-32 == fp_tanh(fp_neg(two), fixed_point_bits));
}


void test_sigmoid_basic(void) {
    int fixed_point_bits = 8;
    int16_t one_half = 1 << (fixed_point_bits - 1);
    int16_t zero = 0;
    int16_t one = 1 << (fixed_point_bits);
    int16_t two = 1 << (fixed_point_bits + 1);

    assert(one_half == fp_sigmoid(zero, fixed_point_bits));
    assert(186 == fp_sigmoid(one, fixed_point_bits));
    assert(70 == fp_sigmoid(fp_neg(one), fixed_point_bits));
    assert(224 == fp_sigmoid(two, fixed_point_bits));
    assert(32 == fp_sigmoid(fp_neg(two), fixed_point_bits));
}
